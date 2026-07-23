from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# 完全版打包时把 llama-server.exe + 模型文件放在这个相对目录下（见
# scripts/build_full.py）；精简版没有这个目录，find_bundled_engine 会返回 None，
# ENGINE_LOCAL 照旧走用户手填的外部 Base URL，行为不受影响。
_LOCAL_ENGINE_SUBDIR = Path("resources") / "local_engine"
_ENGINE_EXE_NAME = "llama-server.exe"
_MODEL_FILE_NAME = "sakura-7b-qwen2.5-v1.0-q6k.gguf"

# 汇报给 /v1/chat/completions 的 model 字段——llama-server 单模型模式下不校验这个
# 值，只是用来在日志/UI 里跟"用户手填的本地模型名"区分开。
LOCAL_ENGINE_MODEL_ALIAS = "sakura-7b-qwen2.5-v1.0-q6k"

_LOCAL_ENGINE_HOST = "127.0.0.1"
# -c：上下文长度；--n-gpu-layers 给一个远大于 7B 模型实际层数的值，等价于"全部
# 层丢 GPU"（llama.cpp 的常见写法，多出来的部分会被自动截断，不会报错）。
_CONTEXT_SIZE = 4096
_GPU_LAYERS = 999


@dataclass(frozen=True)
class BundledEngine:
    """精简版没有这两个文件，find_bundled_engine 返回 None——调用方不需要另外
    判断"是不是完全版"，有没有这个对象本身就是唯一判据。"""

    exe_path: Path
    model_path: Path


def get_app_root() -> Path:
    """PyInstaller onedir 打包后 sys.frozen 为 True，可执行文件旁边就是
    resources/ 目录；开发环境（跑源码/pytest）里用项目根目录，方便本地放一份
    resources/local_engine/ 测试而不用真的打包一次。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def find_bundled_engine(app_root: Path | None = None) -> BundledEngine | None:
    root = app_root if app_root is not None else get_app_root()
    exe_path = root / _LOCAL_ENGINE_SUBDIR / _ENGINE_EXE_NAME
    model_path = root / _LOCAL_ENGINE_SUBDIR / _MODEL_FILE_NAME
    if exe_path.is_file() and model_path.is_file():
        return BundledEngine(exe_path=exe_path, model_path=model_path)
    return None


def _find_free_port() -> int:
    # 绑定端口 0 让操作系统分配一个当前空闲端口，立刻关闭连接把端口让出来给
    # 马上要起的子进程用。这中间有个理论上的 TOCTOU 窗口（别的进程抢先占用
    # 同一端口），但这是本机单用户桌面应用，实际发生概率可以忽略。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_LOCAL_ENGINE_HOST, 0))
        return sock.getsockname()[1]


class LocalEngineStartupError(RuntimeError):
    """子进程没能在超时内变得可用——启动失败（比如 CUDA dll 缺失、显存不够崩溃
    退出）或者单纯加载模型比预期慢，两种情况调用方都只能停止等待、把 log_path
    展示给用户，这里不区分。"""


class LocalEngineProcess:
    """管内置 llama-server.exe 子进程本身，不碰 GUI/Qt，方便单测时把
    subprocess.Popen 和 httpx 请求都 mock 掉。同一个实例可以 start() 一次、
    stop() 任意次（包括从没 start() 过），stop() 之后可以再次 start()。"""

    def __init__(self, engine: BundledEngine, *, transport: httpx.BaseTransport | None = None):
        self._engine = engine
        # 测试用注入点：换成 httpx.MockTransport 就能在不碰真实网络的情况下验证
        # wait_until_ready 的轮询/判断逻辑。
        self._transport = transport
        self._process: subprocess.Popen | None = None
        self._base_url: str | None = None
        self._log_path: Path | None = None

    @property
    def base_url(self) -> str | None:
        return self._base_url

    @property
    def log_path(self) -> Path | None:
        return self._log_path

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> str:
        if self.is_running():
            assert self._base_url is not None
            return self._base_url

        port = _find_free_port()
        self._base_url = f"http://{_LOCAL_ENGINE_HOST}:{port}/v1"

        # stdout/stderr 落文件而不是 PIPE：PIPE 不被读取、子进程输出量一大会把
        # 管道缓冲区撑满导致子进程阻塞在 write() 上（llama-server 加载模型时会
        # 持续打印进度），落文件既不会阻塞子进程，启动失败时也有地方看原因。
        log_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", prefix="llama-server-", delete=False, encoding="utf-8"
        )
        self._log_path = Path(log_file.name)

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [
                str(self._engine.exe_path),
                "-m",
                str(self._engine.model_path),
                "--host",
                _LOCAL_ENGINE_HOST,
                "--port",
                str(port),
                "-c",
                str(_CONTEXT_SIZE),
                "--n-gpu-layers",
                str(_GPU_LAYERS),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        log_file.close()
        return self._base_url

    def wait_until_ready(self, timeout: float) -> bool:
        """轮询 /v1/models 直到 200（模型加载进显存期间请求会直接连接失败或
        超时，不算异常，继续轮询）。子进程如果提前退出（崩溃）就没必要傻等到
        超时，直接判失败。"""
        if self._process is None or self._base_url is None:
            return False

        deadline = time.monotonic() + timeout
        url = self._base_url.rstrip("/") + "/models"
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                logger.error("llama-server 进程提前退出，日志见 %s", self._log_path)
                return False
            try:
                with httpx.Client(timeout=2.0, transport=self._transport) as client:
                    resp = client.get(url)
                if resp.status_code < 500:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        return False

    def stop(self) -> None:
        if self._process is None:
            return
        process, self._process = self._process, None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._base_url = None
