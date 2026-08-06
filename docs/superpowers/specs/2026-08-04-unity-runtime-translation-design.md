# Unity 游戏运行时外挂翻译设计

## 背景

现有 `EngineAdapter`（`engines/base.py`）体系是"离线静态"模式：`detect_adapter()` 扫工程目录文件特征 → `extract()` 读出 `TextUnit` 列表 → 批量调 LLM 翻译、存 `Store`（`units.db`）→ `inject()` 把译文写回工程文件本体，全程不需要游戏跑起来。这套模式成立是因为 RPG Maker/WOLF RPG 的文本本来就以 JSON/文本形式摊在磁盘工程文件里。

Unity 游戏不是这样：文本大多编译进 AssetBundle/IL2CPP 元数据里，没有能直接读写的"工程文件"。要做到侵入性最低（不改游戏文件本体、可完全卸载还原），业界成熟方案是运行时 mod 注入——往游戏目录塞 BepInEx（.NET mod 加载器）+ XUnity.AutoTranslator（UI 文本 hook 插件），游戏进程启动时通过 DLL 搜索路径劫持（把 `winhttp.dll`/`version.dll` 伪装成代理）自动加载，运行时实时截获渲染前的字符串、调用外部翻译接口、原地替换显示。

本设计让 rpgTranslator 支持这条路径：拖拽识别到 Unity 工程后，自动判断 Mono/IL2CPP、x86/x64，部署对应的 BepInEx+XUnity 变体，并把 XUnity 的翻译请求路由到本项目自己的 LLM 调用逻辑（而不是 Google/DeepL 等 XUnity 内置的翻译源）。

## 范围

包含：
1. Unity 工程探测（新增探测器，接入现有 `REGISTERED_ADAPTERS` 式的链式识别，但不实现 `EngineAdapter` 接口——见下方"为什么不复用 EngineAdapter"）。
2. Mono/IL2CPP + x86/x64 自动判定，选中对应离线打包好的 BepInEx+XUnity 变体。
3. mod 部署/卸载（文件复制、配置生成、覆盖前备份、精确回滚）。
4. 本地翻译 shim 服务器：实现 XUnity 的 CustomTranslate 端点契约，内部转调用本项目现有 `LLMClient`，配一套 Unity 专用的占位符保护 + 单条翻译 prompt。
5. GUI 集成：识别到 Unity 工程后主界面阶段 2/3 切换成"部署/卸载"面板；本地 Sakura 引擎在 Unity 工程下给出警告文案（不拦截）。
6. BepInEx+XUnity 二进制素材的获取方式（构建期下载脚本，不进 git）。

不包含：
- 静态提取 Unity 游戏文本（解包 AssetBundle 改 TextAsset 再重新封包）——侵入性更高，且不同游戏资源组织方式差异极大通用性差，本设计不做，是独立方向。
- 术语表/翻译记忆跨会话持久化——shim 无状态，翻译记忆交给 XUnity 自身的本地缓存文件，不接现有 `Store`。
- 游戏内翻译叠加层的自定义 UI——直接用 XUnity 自带的热键覆盖层（ALT+0/T/R/F），不重新做一套。
- Linux/macOS 支持——只做 Windows（跟现有项目平台范围一致）。

## 为什么不复用 EngineAdapter

`EngineAdapter.extract`/`inject` 的语义是"读工程文件→写工程文件"，Unity 运行时翻译没有这两个动作——没有可提取的静态文本清单，也没有"注入"这个写回步骤（部署 mod 后翻译发生在游戏运行期间，不产出译文文件）。硬套这个接口（比如让 `extract` 返回空列表、`inject` 去做部署）会让接口语义名不副实，后续维护者看到 `adapter.inject()` 会以为在写译文回工程文件。

因此新增一个平行入口 `UnityRuntimeAdapter`，不实现 `EngineAdapter` 抽象基类，`pipeline.py` 现有的 `run_extract`/`run_translate`/`run_inject`/`run_full` 都不覆盖这条路径，GUI 层直接调新模块 `unity/` 下的函数。`detect_adapter()` 本身保持只服务 `EngineAdapter` 家族不变；新增一个独立的 `detect_unity(project_dir) -> bool`，GUI 拖拽回调里先后调用两个探测入口决定走哪条分支。

## 架构 / 数据流

```
拖拽游戏目录
      │
      ├─ detect_adapter() 命中 ─→ 现有 提取/翻译/注入 三段式（不动）
      │
      └─ detect_unity() 命中 ─→ Unity 分支：
                │
                ▼
         probe_unity_target()：判定 Mono/IL2CPP、x86/x64、定位 exe 路径
                │
                ▼
         ModDeployer.deploy()：复制对应变体的 BepInEx+XUnity 文件到游戏目录，
                                写 AutoTranslatorConfig.ini 指向本地 shim 端口，
                                写 manifest.json 记录本次新增/覆盖了哪些路径
                │
                ▼
         TranslateShimServer：桌面 App 内常驻的本地 HTTP server，
                                实现 XUnity CustomTranslate 契约
                │
                ▼
         用户自己启动游戏（Steam/直接跑 exe 都行；doorstop 在进程启动那一刻
                          自动生效，不需要本项目拉起游戏进程）
                │
                ▼
         ModRemover.remove()：读 manifest，删除新增文件、还原被覆盖前备份的
                               原文件（如果有）
```

### 1. 探测（`unity/detect.py`）

```python
def detect_unity(project_dir: Path) -> UnityTarget | None:
    # 找 <ExeName>.exe 同级的 <ExeName>_Data/ 目录，且其中含
    # globalgamemanagers 或 data.unity3d，才判定为 Unity 工程；
    # 目录下可能有多个 exe（比如自带的 crash handler），排除明显不是主程序的。
    ...

@dataclass(frozen=True)
class UnityTarget:
    exe_path: Path
    data_dir: Path
    backend: Literal["mono", "il2cpp"]
    arch: Literal["x86", "x64"]
```

- `backend` 判定：`<ExeName>_Data/../GameAssembly.dll`（exe 同级）存在 → `il2cpp`；否则看 `<ExeName>_Data/Managed/Assembly-CSharp.dll` 存在 → `mono`。两者都没有则判定失败，`detect_unity` 返回 `None`（GUI 侧提示"识别为 Unity 目录结构，但无法确定运行时后端，暂不支持"，不强行按某个默认值瞎猜）。
- `arch` 判定：直接读 exe 的 PE 头——`e_lfanew`（偏移 `0x3C` 处的 4 字节）定位到 PE 头起始，其后 4 字节是 `Signature`，再 2 字节是 `Machine` 字段，`0x014c`=x86，`0x8664`=x64。纯字节读取，不引入 pefile 之类的第三方依赖，十几行代码。

### 2. mod 部署（`unity/deploy.py`）

```python
_VARIANT_DIR = {
    ("mono", "x86"): "mono_x86",
    ("mono", "x64"): "mono_x64",
    ("il2cpp", "x86"): "il2cpp_x86",
    ("il2cpp", "x64"): "il2cpp_x64",
}

def deploy(target: UnityTarget, shim_port: int, resources_root: Path) -> DeployResult:
    # 1. variant_dir = resources_root / "unity_mod" / _VARIANT_DIR[(target.backend, target.arch)]
    # 2. 遍历 variant_dir 下所有文件，对应到 target.exe_path.parent 下的相对路径；
    #    目标路径已存在的文件先备份到 <game_dir>/.rpg_translator_backup/unity_original/
    #    （只在还没备份过时才备份，逻辑照抄 pipeline._stash_original_variant 的"只在
    #    第一次"哲学，避免多次部署把上一次自己写的文件误当成"原文件"备份）；
    # 3. 复制 variant_dir 内容覆盖过去；
    # 4. 生成/覆写 BepInEx/config/AutoTranslatorConfig.ini：Endpoint=CustomTranslate，
    #    [Custom] 段 Url=http://127.0.0.1:{shim_port}/translate（协议见下方
    #    "翻译 shim 服务器"一节，GET + from/to/text 查询参数，纯文本响应）；
    # 5. 把这次实际新增/覆盖的相对路径列表写入 <game_dir>/.rpg_translator_unity/manifest.json；
    # 6. 返回 DeployResult(manifest_path, config_path, ...) 给 GUI 展示。
```

### 3. mod 卸载（`unity/deploy.py`）

```python
def remove(game_dir: Path) -> RemoveResult:
    # 读 manifest.json；对每个记录的路径：
    #   - 如果 unity_original 备份里有对应文件，复制回来（还原覆盖前的原状）；
    #   - 否则（本来就不存在，是纯新增），直接删除；
    # 删完清掉 manifest.json 和空的 BepInEx/ 目录（如果整个删空了）。
    # manifest 不存在（用户没部署过或已经卸载过）：直接返回"无需卸载"，不报错。
```

### 4. 翻译 shim 服务器（`unity/translate_shim.py`）

- 桌面 App 内起一个本地 HTTP server（复用 `gui/workers.py` 的后台线程/异步任务风格，跟随 App 生命周期启停），监听空闲端口，实际端口号在部署时写入 `AutoTranslatorConfig.ini`。
- 实现 XUnity 官方的 CustomTranslate 端点契约（README 已确认协议）：`AutoTranslatorConfig.ini` 里配 `Endpoint=CustomTranslate`，`[Custom]` 段 `Url=http://127.0.0.1:{shim_port}/translate`；XUnity 发起纯 `GET` 请求，query string 带 `from`/`to`/`text` 三个参数（`text` 是待翻译原文），响应体是**纯文本**译文（不是 JSON，不包裹任何结构）。README 特别提示"优先用 HTTP 不用 HTTPS，因为 unity-mono 处理 SSL 常有问题"——shim 本来就是本机 HTTP，天然符合。
- 单次请求处理流程：
  1. **占位符保护**（新模块 `unity/placeholders.py`，不复用 `codec/control_codes.py`——那套是 RPG Maker `\C[n]`/`\N[n]`/`\V[n]` 专属转义语法，Unity 没有这套标准）。第一版只做通用兜底，覆盖：
     - TextMeshPro 富文本标签：`<color=...>`、`<b>`、`</b>`、`<size=...>` 等尖括号标签
     - 花括号占位符：`{0}`、`{player_name}` 等
     - `printf` 风格格式化符：`%s`、`%d`、`%1$s` 等
     - 转义换行 `\n`
     未覆盖到的游戏特定标记允许译文里偶尔漏保护，后续按实际反馈补规则，不追求开局覆盖所有游戏。
  2. **单条翻译 prompt**（新模块，同样不复用 `batch_translator.py` 的 `_TRANSLATE_SYSTEM_PROMPT`——确认过这套连"在线默认"策略都绑死了 RPG Maker 的 `⟦CCn⟧` 控制码占位符约定和"人名对照"批量结构，不是通用日译中/英译中 prompt）。新 prompt 精简、无历史/无术语表（shim 无状态），只做"保留占位符 token 原样、只输出译文"这类通用约束。
  3. 调 `LLMClient`（现有模块，纯 HTTP 传输/重试/fallback，跟文本语法无关，是唯一原样复用的现有翻译基础设施）发请求。
  4. 还原占位符，原样返回译文文本。
- 无状态：不查、不写 `Store`。翻译记忆完全交给 XUnity 自己写的本地缓存文件，重复文本第二次根本不会打到 shim。

### 5. GUI 集成（`gui/main_window.py`）

- `_on_path_dropped`：`detect_adapter` 识别失败后，再试 `detect_unity`；命中则记录 `UnityTarget`，切换阶段 2/3 的 `QGroupBox`（用 `QStackedWidget` 包一层，或者简单 `setVisible` 切换现有"翻译/注入"两个 GroupBox 和新增的"部署/卸载"GroupBox）。
- 新增按钮：「部署翻译外挂」（触发 probe + deploy，成功后展示"请自行启动游戏"提示 + 「打开游戏文件夹」快捷按钮，复用现有同名按钮逻辑）、「卸载还原」（触发 remove）。
- 设置面板当前引擎是 `ENGINE_LOCAL`（Sakura）时，部署面板顶部加一行提示："Sakura 是 RPG Maker 语法特化模型，翻译 Unity 游戏建议在设置里切换在线 Provider"——仅提示，不禁用部署按钮。

### 6. BepInEx + XUnity.AutoTranslator 二进制素材

不进 git 仓库（体积大、是第三方二进制）。照抄现有 `scripts/build_full.py` 对 llama.cpp/模型文件的处理模式：新增 `scripts/fetch_unity_mod_assets.py`，构建期（或开发者首次跑一次）下载官方 GitHub Release zip，解压落到 `resources/unity_mod/{mono_x86,mono_x64,il2cpp_x86,il2cpp_x64}/`，`resources/unity_mod/` 加进 `.gitignore`。下载函数复用 `build_full.py` 已有的 sha256 校验 + 断点续传 + 重试三层保护逻辑（不重新造轮子）。

已确认可直连下载（不需要代理）：
- BepInEx Mono（稳定版 v5.4.23.5）：`BepInEx_win_x64_5.4.23.5.zip` / `BepInEx_win_x86_5.4.23.5.zip`
- BepInEx IL2CPP（v5 稳定版没有 IL2CPP 支持，只能用 v6 pre-release）：`BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip` / `BepInEx-Unity.IL2CPP-win-x86-6.0.0-pre.2.zip`
- XUnity.AutoTranslator v5.6.1：`XUnity.AutoTranslator-BepInEx-5.6.1.zip`（叠加进 Mono 变体）/ `XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip`（叠加进 IL2CPP 变体）

`find_bundled_engine` 那套"frozen 用 exe 目录、开发环境用项目根目录"的 `app_root` 解析方式直接照搬到 `resources/unity_mod/` 的定位上。

**已知限制**：IL2CPP 路径依赖 BepInEx 6 pre-release（v5 稳定版没有 IL2CPP 支持），比 Mono 路径（BepInEx 5 稳定版）成熟度低，出问题概率更高，这是上游生态现状，不是本设计能绕开的。IL2CPP 变体因为要带一份 .NET 运行时（`dotnet/` 目录）体积明显更大（231 个文件、75MB+，Mono 版只有 22 个文件不到 2MB）。

**实测拉取后确认的几个点**（写部署代码前先摸过一遍真实 zip 内容，见 `resources/unity_mod/SOURCES.md`）：
- `doorstop_config.ini` 由 BepInEx 官方 zip 自带，Mono/IL2CPP 两版内容不同（`target_assembly` 指向的 dll 不同，IL2CPP 版多一个 `[Il2Cpp]` 段指向 `dotnet/coreclr.dll`），部署时原样复制、不用我们生成，`deploy()` 的"整棵目录树原样复制"逻辑天然覆盖这点，不需要特殊处理。
- 没有任何一个官方 zip 自带 `BepInEx/config/AutoTranslatorConfig.ini`——插件自己的说明只写"配置文件在游戏启动时自动生成"，没有在文档里显式给出这个路径。`BepInEx/config/AutoTranslatorConfig.ini` 是社区广泛验证过的实际路径，但这份第一方 README 本身没有逐字背书，`deploy()` 按这个路径写入是有依据的最佳判断，不是编造，但**需要在真机部署一次实测确认**——XUnity 首次启动时是直接读取这份已存在的 ini，还是会用默认值覆盖它，这是唯一没有把握、必须实测才能确认的点。
- XUnity 插件 zip 里除了 `XUnity.AutoTranslator/` 还带了一个独立插件 `XUnity.ResourceRedirector/`（贴图/字体资源重定向，跟翻译无关）。BepInEx 会自动加载 `plugins/` 下所有 dll，带着这个等于多启用一个本设计不需要的插件，不符合"侵入性最低"的原则——`fetch_unity_mod_assets.py` 合并 XUnity zip 时显式跳过这个子目录。

## 测试

- `unity/detect.py`：PE 头解析（x86/x64 各构造一个最小合法 PE 头字节串）、Mono/IL2CPP 判定分支、目录结构不匹配时返回 `None`，纯函数单测，不需要真实 Unity 游戏。
- `unity/deploy.py`：临时目录构造假游戏目录 + 假 `resources/unity_mod/<variant>/`（占位字节文件，不需要真实 BepInEx 二进制内容），跑 `deploy()` 断言文件落地位置 + config 内容 + manifest 内容；再跑 `remove()` 断言精确回滚（新增的删掉、被覆盖的还原）。覆盖"游戏目录本来就有同名文件"这个备份分支。
- `unity/placeholders.py`：保护/还原往返测试，覆盖设计里列的四类占位符，包括嵌套/连续多个占位符不串扰的情况。
- `unity/translate_shim.py`：起本地 server 发模拟 XUnity 请求，断言协议契约正确；`LLMClient` 调用部分 mock 掉，验证"占位符保护→调用→还原"整条链路组装正确，不实际打外部 API。
- GUI 侧：沿用 `test_gui.py` 现有对 `main_window` 的测试方式（mock 掉 deploy/probe），补 `detect_unity` 命中时面板切换、Sakura 警告文案展示的分支。
- `scripts/fetch_unity_mod_assets.py`：跟 `build_full.py` 一样不在自动化测试/CI 里跑（要下载外部文件），只单测内部的路径拼装/sha256 校验逻辑，不联网。
- 真机验证：有真实 Unity 游戏样本的话，走一遍部署 + 手动启动游戏，肉眼确认 XUnity 面板弹出、文本被替换；没有样本的话这一层只能靠"文件/协议层正确"的单测覆盖，实际游戏内表现无法自动化验证，会在交付时明确说明这个局限。
