# 内置本地模型（完全版 release）设计

## 背景

现有 `ENGINE_LOCAL` 模式要求用户自己在别处部署一个 OpenAI 兼容的本地推理服务（比如 Ollama + Sakura GGUF），手填 Base URL/模型名。这对不懂本地部署的用户是道门槛。目标是发一个"完全版" release，把推理引擎 + 模型文件直接打包进去，用户装完选"本地模型"就能用，不用自己去装 Ollama、拉模型。

## 范围

1. 新增"完全版"打包产物：在现有 `scripts/build.py`（onedir Windows exe）基础上，塞入 llama.cpp 官方预编译 CUDA build（`llama-server.exe` + cudart 运行时 dll）+ `sakura-7b-qwen2.5-v1.0-q6k.gguf` 模型文件，7z 分卷输出（过 GitHub Release 单文件 2GB 上限）。
2. 运行时：`MainWindow` 检测到本地打包了引擎+模型文件、且用户没手填本地 Base URL/模型名时，点「开始翻译」自动拉起 `llama-server.exe` 子进程、探测空闲端口、等待就绪，再继续走原有翻译流程；App 关闭时杀掉子进程。
3. "精简版"（现有 `scripts/build.py` 产物）不受影响：没有内置引擎/模型文件，`ENGINE_LOCAL` 行为跟现在完全一样，用户仍要自己填外部 Base URL。
4. 用户在设置里手填了本地 Base URL/模型名的情况，即使是完全版也不去抢占——尊重用户显式配置，不自动拉起内置引擎。

不在范围内：CPU-only build（没 GPU 的用户用精简版接云端 API）、模型量化版本可选（固定用 q6k）、自动下载模型（完全版模型文件是打包进安装包的，不是运行时下载）、把内置引擎做成除"点开始翻译"外其它时机常驻启动。

## 架构 / 数据流

### 1. 引擎二进制与模型的获取（`scripts/build_full.py`，新增）

不复用 `scripts/build.py` 的 CLI，而是导入它的 `main()` 复用 PyInstaller 步骤，再追加：

```
run scripts.build.main()                       # 产出 dist/RPGTranslator/
下载 llama-b<BUILD>-bin-win-cuda-12.4-x64.zip   # 主二进制（含 llama-server.exe）
下载 cudart-llama-bin-win-cuda-12.4-x64.zip     # cudart/cublas 运行时 dll（主二进制包不含，是分开发布的）
解压两者到 dist/RPGTranslator/resources/local_engine/
下载 sakura-7b-qwen2.5-v1.0-q6k.gguf 到同一目录
用 py7zr 把 dist/RPGTranslator/ 分卷打包成 dist/RPGTranslator-full-v<version>.7z.001 / .002 / ...
```

选 CUDA 12.4 变体而非 13.3：驱动版本要求更宽松，兼容更多用户现有的 NVIDIA 驱动。

关键点：
- llama.cpp release tag、资源文件名、模型文件 URL 都是写死的常量（`_LLAMA_CPP_RELEASE_TAG` 等），不自动追新版本——追新版本要人工验证过 CUDA build 能正常跑起来才能改。
- 下载帮助函数支持传入预期 sha256（可选）：给了就校验，校验失败直接报错退出，不静默用坏文件继续打包；没给就下载后打印出实际 sha256，供维护者第一次跑完后回填进常量里，后续 CI/其他机器复现构建时就能校验。
- 这个脚本本身**不在自动化测试/CI 里跑**（要下载 10GB+ 文件），只做单元测试覆盖其内部函数（下载校验、解压筛选、分卷）。真正生成完全版安装包是维护者手动跑一次的操作。
- 下载函数三层保护，专门照顾大陆访问 GitHub Release/HuggingFace 常见的"连得上但不稳"：本地缓存命中（`--work-dir` 下已有且校验通过/没给校验值就信任）直接跳过；`.part` 临时文件 + HTTP Range 续传，网络中途断线不用从头来；传输层异常指数退避重试。另外支持 `LLAMA_CPP_RELEASE_BASE_URL`/`HF_ENDPOINT` 两个环境变量分别替换 GitHub release 前缀和 HuggingFace 域名（镜像/自建反代），`HTTPS_PROXY`/`HTTP_PROXY` 走 httpx 默认的 `trust_env` 行为，不用额外代码。

### 2. 内置引擎检测与生命周期（新增 `src/rpg_translator/translate/local_engine.py`）

```python
@dataclass(frozen=True)
class BundledEngine:
    exe_path: Path
    model_path: Path

def find_bundled_engine(app_root: Path | None = None) -> BundledEngine | None:
    # app_root 默认：frozen（PyInstaller）用 sys.executable 所在目录，否则项目根目录
    # 检查 resources/local_engine/llama-server.exe + resources/local_engine/<model文件名>
    # 两者都存在才返回 BundledEngine，否则 None（精简版走这条）

class LocalEngineProcess:
    def start(self) -> str:  # 返回 base_url，比如 http://127.0.0.1:51234/v1
        # 找空闲端口 -> subprocess.Popen(启动 llama-server.exe，--host 127.0.0.1 --port <port> -m <model_path> ...)
        # Windows 下加 CREATE_NO_WINDOW，不弹控制台窗口
    def wait_until_ready(self, timeout: float) -> bool:
        # 轮询 GET {base_url}/models，200 即就绪；模型加载到显存可能要十几秒到几十秒
    def stop(self) -> None:
        # terminate() -> 限时 wait() -> 还活着就 kill()；没启动过/已经停了调用直接返回，不抛异常
```

`LocalEngineProcess` 只管子进程本身，不碰 GUI/Qt，方便单测（mock `subprocess.Popen` 和 httpx 调用）。

### 3. GUI 接入（`gui/main_window.py` + `gui/workers.py`）

新增 `LocalEngineStartWorker(QThread)`（`workers.py`，跟其它 Worker 同一风格）：接收一个 `LocalEngineProcess`，`run()` 里调 `start()` + `wait_until_ready()`，成功 emit `finished_ok(base_url)`，超时/异常 emit `failed(str)`。

`MainWindow`：
- 新增实例属性 `self._local_engine_process: LocalEngineProcess | None = None`、`self._bundled_local_base_url: str | None = None`。
- `_on_start_clicked`：engine 为 `ENGINE_LOCAL` 且 `local_base_url`/`local_model` 为空时，原来直接报错拦住；改成先查 `find_bundled_engine()`——查到就不报错，起 `LocalEngineStartWorker`（若 `self._local_engine_process` 还没建过或已停止才新建，避免重复加载模型），成功回调里把 `base_url` 存进 `self._bundled_local_base_url`，再继续原来的 `ExtractWorker` 流程；查不到才维持原有"请先在设置里配置"报错。
- `_start_translate_worker`：`resolve_local_config` 取到的 `base_url`/`model` 为空、且 `self._bundled_local_base_url` 有值时，用后者 + 固定的内置模型别名，不去动 `settings_dialog.py`（用户手填的配置优先级不变，只是给"没手填"这个空档补一条内置路径）。
- `closeEvent`：现有等 worker 收尾的逻辑跑完、真正 `event.accept()` 之前，调 `self._local_engine_process.stop()`（`None` 时跳过）。
- 「重试失败项」复用同一个已经在跑的子进程，不重新拉起（模型加载有实打实的耗时，没必要每次重试都重来）。

### 4. 授权与署名

`sakura-7b-qwen2.5-v1.0-q6k.gguf` 许可证是 CC-BY-NC-SA-4.0（署名、非商业、相同方式共享）。RPGTranslator 本身免费非商用，满足非商业前提。完全版需要在 About/README 里加一段署名 + 协议链接，指向 `SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF`。

## 测试

- `local_engine.py`：`find_bundled_engine` 的存在/缺失分支；`LocalEngineProcess.start/wait_until_ready/stop` 用 mock 掉 `subprocess.Popen` 和 httpx 请求测状态流转，不真的起进程。
- `build_full.py`：下载校验函数（sha256 校验通过/失败两条路径）、解压筛选逻辑、分卷函数，用小的假文件跑，不联网。
- GUI 侧：沿用 `test_gui.py` 现有对 `main_window` 的测试方式（mock 掉 worker），补 `find_bundled_engine` 返回非空时 `_on_start_clicked` 不再报错、能起 `LocalEngineStartWorker` 的分支。
