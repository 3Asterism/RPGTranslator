"""打包"完全版"：在精简版基础上塞入 llama.cpp 官方预编译 CUDA 引擎 + Sakura
GGUF 模型文件，7z 分卷输出（过 GitHub Release 单文件 2GB 上限）。设计背景见
docs/superpowers/specs/2026-07-23-bundled-local-model-design.md。

用法：.venv\\Scripts\\python.exe scripts\\build_full.py
产出：dist\\RPGTranslator-full-v<version>.7z.001 / .002 / ...

这个脚本会下载 10GB+ 文件（llama.cpp CUDA 二进制 + cudart 运行时 + 6.25GB 的
q6k 模型），不在自动化测试/CI 里跑——是维护者手动跑一次的操作，看网络情况要
几分钟到几十分钟。自动化测试只覆盖脚本内部的纯函数（下载校验/解压筛选/分卷），
不联网、不跑这个 main()。

下载支持断点续传 + 重试 + 本地缓存命中跳过（见 download() 的说明），--work-dir
下已经下过的文件默认直接复用，不用 --force-redownload 就不会重新拉一遍。大陆
网络访问 GitHub Release/HuggingFace 经常不稳，可以配合下面几个环境变量：
- `HTTPS_PROXY`/`HTTP_PROXY`：httpx 默认读这两个（比如本机开了 Clash），不用
  改代码
- `LLAMA_CPP_RELEASE_BASE_URL`：整段替换 llama.cpp release 的下载前缀（自建
  反代/镜像地址）
- `HF_ENDPOINT`：替换 HuggingFace 域名（大陆常用 https://hf-mirror.com），跟
  huggingface_hub 官方用的环境变量同名
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile
from pathlib import Path
from typing import Callable

import httpx
import multivolumefile
import py7zr

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
APP_DIR = DIST_DIR / "RPGTranslator"
LOCAL_ENGINE_DIR = APP_DIR / "resources" / "local_engine"

# --- llama.cpp 官方预编译 CUDA build ---
# 写死具体 release tag，不自动追新版本——追新版本要人工验证过新 build 能正常跑
# 起来（尤其是 cudart 版本兼容性）才能改这几个常量。选 cu12.4 而不是 cu13.3：
# 驱动版本要求更宽松，兼容更多用户现有的 NVIDIA 驱动。主二进制包本身不含
# cudart/cublas 运行时 dll，是官方分开发布的，两个都要下。
_LLAMA_CPP_RELEASE_TAG = "b10092"
# 大陆访问 GitHub Release 经常连不上/巨慢——支持用环境变量 LLAMA_CPP_RELEASE_BASE_URL
# 整段替换成自建反代/镜像地址（比如 ghproxy 类服务的完整前缀），不改代码。
_LLAMA_CPP_BASE_URL = os.environ.get(
    "LLAMA_CPP_RELEASE_BASE_URL",
    f"https://github.com/ggml-org/llama.cpp/releases/download/{_LLAMA_CPP_RELEASE_TAG}",
)
_LLAMA_CPP_MAIN_ASSET = f"llama-{_LLAMA_CPP_RELEASE_TAG}-bin-win-cuda-12.4-x64.zip"
_LLAMA_CPP_CUDART_ASSET = "cudart-llama-bin-win-cuda-12.4-x64.zip"
# 校验值留空表示"还没人跑完这个版本、拿到过实际 sha256"——download() 会在下载
# 完之后打印出实际值，维护者确认下载内容没问题后回填进这里，后续在别的机器/CI
# 上复现同一个完全版构建时就能校验，不会悄悄用一份被替换/损坏的文件继续打包。
_LLAMA_CPP_MAIN_SHA256: str | None = None
_LLAMA_CPP_CUDART_SHA256: str | None = None

# --- Sakura 模型文件 ---
# 许可证 CC-BY-NC-SA-4.0（署名、非商业、相同方式共享）——完全版发布说明里要带
# 这个仓库的署名和协议链接，见 spec 里的"授权与署名"一节。
# HF_ENDPOINT 是 huggingface_hub 官方就支持的镜像切换环境变量（大陆常用
# hf-mirror.com），这里沿用同一个变量名而不是自造一个，用户可能已经因为别的
# 工具设置过。
_MODEL_FILE_NAME = "sakura-7b-qwen2.5-v1.0-q6k.gguf"
_HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
_MODEL_URL = f"{_HF_ENDPOINT}/SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF/resolve/main/{_MODEL_FILE_NAME}"
_MODEL_SHA256: str | None = None

_DEFAULT_VOLUME_SIZE_MB = 1900  # 留余量给 GitHub Release 单文件 2GB 上限
# 连接建立比传输更容易在大陆到 GitHub/HF 的线路上卡住，connect 超时给宽松一点；
# httpx.Client 默认 trust_env=True，本机配了 HTTP_PROXY/HTTPS_PROXY（比如 Clash）
# 会自动走代理，不用额外处理。
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0)
_DEFAULT_RETRIES = 5
_RETRY_BACKOFF_CAP_SECONDS = 30.0


class ChecksumMismatchError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    dest: Path,
    *,
    expected_sha256: str | None = None,
    client: httpx.Client | None = None,
    force: bool = False,
    retries: int = _DEFAULT_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """三层保护，专门照顾大陆访问 GitHub Release/HuggingFace 常见的"连得上但
    不稳"：
    1. 本地缓存命中（dest 已存在且校验通过，或没给校验值就直接信任）直接跳过，
       重复跑这个脚本、或者上次中途失败重跑，不用把几 GB 的文件再吃一遍流量；
    2. 用 `<dest>.part` 临时文件 + HTTP Range 续传，网络中途断线不用从头下载；
    3. 传输层异常（连接失败/超时/5xx）按指数退避重试 retries 次，不是一碰到
       瞬时错误就判失败——这两个源在大陆的连接经常是"重试几次总有一次能过"。

    校验失败（下载完但 sha256 跟期望值对不上）直接删掉这份文件再报错，不留一份
    坏文件在磁盘上被后面的步骤当成好的用。
    """
    if dest.is_file() and not force:
        actual = sha256_of(dest)
        if expected_sha256 is None or actual == expected_sha256:
            print(f"[build_full] {dest.name} 命中本地缓存，跳过下载（sha256={actual}）")
            return
        print(f"[build_full] {dest.name} 本地缓存 sha256 跟期望值对不上，丢弃重新下载")
        dest.unlink()

    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.with_name(dest.name + ".part")
    owns_client = client is None
    http = client if client is not None else httpx.Client(follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT)

    try:
        for attempt in range(1, retries + 1):
            resume_from = part_path.stat().st_size if part_path.is_file() else 0
            headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
            try:
                with http.stream("GET", url, headers=headers) as resp:
                    if resp.status_code == 416:
                        break  # 服务器说 Range 起点超出文件范围——说明之前其实已经下完了
                    resp.raise_for_status()
                    resumed = resume_from > 0 and resp.status_code == 206
                    with part_path.open("ab" if resumed else "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if attempt == retries:
                    raise
                wait = min(2**attempt, _RETRY_BACKOFF_CAP_SECONDS)
                print(f"[build_full] {dest.name} 下载中断（第 {attempt}/{retries} 次尝试）：{e}，{wait:.0f}s 后重试")
                sleep(wait)
    finally:
        if owns_client:
            http.close()

    part_path.replace(dest)

    actual = sha256_of(dest)
    if expected_sha256 is None:
        print(f"[build_full] {dest.name} 下载完成，sha256={actual}（未校验——请把这个值回填进脚本常量里）")
        return
    if actual != expected_sha256:
        dest.unlink(missing_ok=True)
        raise ChecksumMismatchError(f"{dest.name} sha256 不匹配：期望 {expected_sha256}，实际 {actual}")


def extract_members(zip_path: Path, dest_dir: Path, wanted_suffixes: tuple[str, ...]) -> list[Path]:
    """只解出文件名匹配 wanted_suffixes 的成员，摊平到 dest_dir（不保留 zip
    内部的子目录结构）——llama.cpp release zip 不同版本之间，二进制有的在
    子目录里有的直接在根目录，按文件名后缀匹配比按路径匹配更稳。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not name.lower().endswith(wanted_suffixes):
                continue
            target = dest_dir / name
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target)
    return extracted


def split_archive(
    source_dir: Path, archive_path: Path, *, volume_size_bytes: int
) -> list[Path]:
    """把 source_dir 整个打成 7z 分卷，卷名 <archive_path>.001/.002/...（用
    ext_digits=3 而不是 multivolumefile 默认的 4 位，跟 7-Zip 官方分卷习惯的
    .7z.001 命名对齐，主流解压工具认得这个命名会自动提示合并）。"""
    volume = multivolumefile.MultiVolume(archive_path, mode="wb", volume=volume_size_bytes, ext_digits=3)
    with volume:
        with py7zr.SevenZipFile(volume, "w") as archive:
            archive.writeall(source_dir, source_dir.name)
    return sorted(archive_path.parent.glob(archive_path.name + ".*"))


def read_app_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-app-build",
        action="store_true",
        help="跳过 PyInstaller 打包，复用 dist/RPGTranslator/ 现有产物",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DIST_DIR / "_build_full_cache",
        help="下载缓存目录（默认 dist/_build_full_cache）",
    )
    parser.add_argument(
        "--volume-size-mb", type=int, default=_DEFAULT_VOLUME_SIZE_MB, help="7z 分卷单卷大小（MB）"
    )
    parser.add_argument(
        "--force-redownload",
        action="store_true",
        help="忽略 --work-dir 下已有的缓存文件，强制重新下载（默认信任已存在的文件）",
    )
    args = parser.parse_args(argv)

    if not args.skip_app_build:
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "build.py")])
        if result.returncode != 0:
            return result.returncode

    if not APP_DIR.is_dir():
        print(
            f"[build_full] 没找到 {APP_DIR}，先跑一遍 scripts/build.py 或者去掉 --skip-app-build",
            file=sys.stderr,
        )
        return 1

    work_dir = args.work_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    main_zip = work_dir / _LLAMA_CPP_MAIN_ASSET
    cudart_zip = work_dir / _LLAMA_CPP_CUDART_ASSET
    model_path = work_dir / _MODEL_FILE_NAME

    print(f"[build_full] 下载 {_LLAMA_CPP_MAIN_ASSET} ...")
    download(
        f"{_LLAMA_CPP_BASE_URL}/{_LLAMA_CPP_MAIN_ASSET}",
        main_zip,
        expected_sha256=_LLAMA_CPP_MAIN_SHA256,
        force=args.force_redownload,
    )
    print(f"[build_full] 下载 {_LLAMA_CPP_CUDART_ASSET} ...")
    download(
        f"{_LLAMA_CPP_BASE_URL}/{_LLAMA_CPP_CUDART_ASSET}",
        cudart_zip,
        expected_sha256=_LLAMA_CPP_CUDART_SHA256,
        force=args.force_redownload,
    )
    print(f"[build_full] 下载 {_MODEL_FILE_NAME}（6.25GB，视网络情况要一段时间）...")
    download(_MODEL_URL, model_path, expected_sha256=_MODEL_SHA256, force=args.force_redownload)

    print(f"[build_full] 组装 {LOCAL_ENGINE_DIR} ...")
    extract_members(main_zip, LOCAL_ENGINE_DIR, (".exe", ".dll"))
    extract_members(cudart_zip, LOCAL_ENGINE_DIR, (".dll",))
    shutil.copy2(model_path, LOCAL_ENGINE_DIR / _MODEL_FILE_NAME)

    version = read_app_version()
    archive_path = DIST_DIR / f"RPGTranslator-full-v{version}.7z"
    print(f"[build_full] 分卷打包到 {archive_path}.001 ...")
    parts = split_archive(
        APP_DIR, archive_path, volume_size_bytes=args.volume_size_mb * 1024 * 1024
    )
    print(f"[build_full] 完成，共 {len(parts)} 个分卷文件：")
    for part in parts:
        print(f"  {part}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
