# RPG Maker 游戏文本提取/翻译/回填工具 — 技术规格与实施指令

> 本文档是写给 Claude Code 的项目简报。这是一个**个人独立项目**，与任何公司/工作项目无关，
> 不要复用、参考或类比任何公司内部基础设施（网关、模型部署、监控栈等），所有依赖都是这个
> 项目自己独立配置的。所有技术选型、架构、格式细节已经调研确认，不需要再做选型讨论。
> 请按里程碑顺序直接开始搭建代码。~~M0-M3 完成后暂停等待人工验收~~——2026-07-17 更新：
> 用户已明确改为"自测通过即自主推进，不用逐里程碑确认"，仅在关键节点简短播报进度，
> 详见第 16 节变更记录。

---

## 1. 角色与目标

实现一个**桌面软件**（不是库、不是纯 CLI 工具），功能和使用体验类似 MTool：

1. 把游戏文件夹或 exe 拖进窗口，自动识别 RPG Maker 引擎版本。
2. 点一个"开始翻译"按钮，自动完成文本提取 → 调用 DeepSeek API 翻译 → 回填。
3. 输出一份可直接运行的汉化版游戏文件夹。

v1 只做**静态打包翻译**（直接改游戏文件生成新拷贝），不做 MTool 那种运行时内存 hook——
后者要在 NW.js 的 V8 环境里做 monkey-patch，老版本 RGSS 引擎更需要 DLL 注入级别的工作，
复杂度和收益不成正比，v1 不做。

核心逻辑（提取/翻译/回填）和 GUI 要解耦：核心做成普通 Python 包，GUI 只是调用方之一，
方便以后单独跑 CLI 调试或者写自动化脚本。

---

## 2. 范围界定（v1）

**必须支持（v1 核心）：**
- RPG Maker MV（NW.js + 明文 JSON）
- RPG Maker MZ（同上，目录结构略有差异）
- RPG Maker VX Ace（Ruby Marshal 二进制）

**必须支持（v1 追加，2026-07-17 与用户确认后加入，见第 16 节变更记录）：**
- RPG Maker XP / VX（老版本 Ruby Marshal，和 VX Ace 同族，复用 M4 的 rubymarshal
  基础设施，但 `RPG::xxx` 类定义有差异，需要单独验证注册）
- WOLF RPG エディター / ウディタ（SmokingWOLF 开发，日本同人游戏圈常用，图标是狼头）——
  **不是 RPG Maker**，专有二进制格式（`.dat` / `.mps`），官方没有公开格式文档。
  可行性基于社区已有的逆向工程先例：`elizagamedev/wolftrans`（Ruby，原始实现，
  MPL-2.0）、`Sinflower/WolfTL`（C++）、`KCFindstr/rewolf-trans`（TypeScript）——
  这些项目证明格式已被破解，但没有一个是 Python 实现、也没有独立的格式规范文档，
  需要先读这些项目的源码把二进制格式逻辑读懂、移植成 Python，细节见第 6.4 节。

**明确不做（v1 之外）：**
- RPG Maker 2000/2003（LcfLMU/LcfLDB 格式，完全不同的解析器，太老，明确排除）
- Unity、Ren'Py、KiriKiri 等其他非 RPG Maker 引擎（WOLF 是唯一例外，见上）
- 运行时 hook / 内存注入模式
- 术语表的复杂人工审核界面（v1 先做最简单的表格展示/编辑）

---

## 3. 技术选型（已确定，不要重新讨论）

- **语言**：Python 3.11+
- **GUI 框架**：**PySide6**（Qt6 官方 Python 绑定，LGPL 协议可免费商用发行）。选它是因为
  拖拽区域、进度条、日志控制台、多线程这几个 MTool 式界面需要的东西 Qt 都是成熟方案，
  PyInstaller 打包的资料也最多、踩坑最少。
- **打包**：PyInstaller，产出 Windows 可执行文件（细节见第 12 节）
- **数据模型**：`pydantic` v2
- **本地存储**：SQLite（标准库 `sqlite3` 或 `sqlmodel`），存 TextUnit 和翻译记忆，支持断点续译
- **HTTP/LLM 调用**：`httpx`（异步），OpenAI 兼容协议
- **API Key 存储**：`keyring` 库，调用系统凭据管理器，**不要把 DeepSeek API Key 明文写进
  配置文件**
- **Ruby Marshal 解析**：`rubymarshal` 包（PyPI 上的 `d9pouces/RubyMarshal`），读写 `.rvdata2`。
  **实施时先验证**：`pip install rubymarshal --break-system-packages` 后拿真实 VX Ace 工程的
  `Actors.rvdata2` 试读，确认库能不能正常解析 RPG Maker 自定义的 `RPG::xxx` 类（README 提到
  只支持基础类型，可能需要按它文档里的自定义类型注册机制补注册）
- **压缩**：标准库 `zlib`（VX Ace `Scripts.rvdata2` 里脚本正文是 zlib 压缩的）
- **CLI（次要接口，用于开发调试，不是主入口）**：`typer`

### DeepSeek API 配置（重要，注意时效性）

```
DEEPSEEK_API_KEY     # 通过 GUI 设置面板输入，走 keyring 存取
DEEPSEEK_BASE_URL    # https://api.deepseek.com
DEEPSEEK_MODEL       # 默认 deepseek-v4-flash，可切换 deepseek-v4-pro
```

客户端按 OpenAI 兼容格式调用（`base_url` 指向 DeepSeek，其余请求体和 OpenAI SDK 基本一致）。

⚠️ **模型名称时效性提醒**：网上大量教程/示例代码里用的模型名是 `deepseek-chat` 和
`deepseek-reasoner`——这两个是旧别名，DeepSeek 官方公告将在 **2026-07-24 15:59 UTC**
停用（写这份文档时只剩几天窗口期），到期后调用会直接报错、没有兼容回退。**必须使用当前
名称 `deepseek-v4-flash`（默认，性价比高，批量翻译够用）和 `deepseek-v4-pro`（需要更高
质量时可选）**，不要照抄旧教程里的模型名。实现时上手前先去 DeepSeek 官方文档
（`api-docs.deepseek.com`）确认一遍当前有效的模型名和 base_url 路径（是否需要 `/v1` 后缀
等细节），不要完全依赖本文档里写的字符串。

DeepSeek 支持 context caching（重复的 prompt 前缀会打折计费），批量翻译场景下值得利用——
如果每次请求都带相同的术语表和 system prompt，把这部分放在 prompt 最前面能吃到缓存折扣，
`llm_client.py` 设计请求结构时留意这一点。

---

## 4. 系统架构

```
project/
  core/
    ir.py                # TextUnit 数据模型、翻译状态枚举
    store.py              # SQLite 存取层：TextUnit + 翻译记忆
    pipeline.py            # extract -> protect -> translate -> restore -> inject 编排，
                            # 提供同步和异步两种入口，供 CLI 和 GUI 分别调用
  engines/
    base.py                 # EngineAdapter 抽象基类
    mv_mz.py                  # RPG Maker MV/MZ adapter
    vxace.py                   # RPG Maker VX Ace adapter
    xp_vx.py                    # RPG Maker XP/VX adapter（复用 rvdata2_codec 的 rubymarshal
                                  # 基础设施，自定义类注册不同，见 6.3）
    wolf.py                       # WOLF RPG エディター adapter（见 6.4，格式无官方文档，
                                    # 基于社区逆向工程先例移植）
  codec/
    control_codes.py            # 控制码占位符提取/还原
    mv_crypto.py                  # rpgmvp/rpgmvo/rpgmvm 解密/加密（按需，见 6.1）
    rvdata2_codec.py                # rubymarshal 封装 + zlib 处理（VX Ace / XP/VX 共用）
    wolf_binary.py                   # WOLF .dat/.mps 二进制读写（见 6.4，纯 struct 手写，
                                       # 没有现成 PyPI 库）
  translate/
    glossary.py                      # 术语抽取
    llm_client.py                      # DeepSeek 客户端封装（OpenAI 兼容）
    batch_translator.py                  # 分批、带上下文的翻译调用
    qa.py                                  # 一致性校验
  gui/
    app.py                                  # PySide6 QApplication 入口
    main_window.py                            # 拖拽区、引擎信息展示、开始按钮、进度条、日志区
    settings_dialog.py                          # API Key（keyring）、模型选择、并发数、输出目录
    workers.py                                    # QThread 包 pipeline，用 Qt Signal 回传进度
    glossary_dialog.py                              # 术语表查看/编辑（简单表格）
  cli.py                                             # typer 入口，开发调试用：extract/translate/inject/run
  config.py                                           # pydantic-settings，QSettings 存非敏感项
```

**EngineAdapter 接口（所有引擎适配器必须实现）：**

```python
class EngineAdapter(ABC):
    @staticmethod
    @abstractmethod
    def detect(project_dir: Path) -> bool:
        """判断这个目录是不是这个引擎的工程"""

    @abstractmethod
    def extract(self, project_dir: Path) -> list[TextUnit]: ...

    @abstractmethod
    def inject(self, project_dir: Path, units: list[TextUnit], output_dir: Path) -> None:
        """把翻译结果写入 output_dir（不要原地覆盖，输出到新目录）"""
```

`detect()` 判断依据：MV 是根目录有 `www/data/System.json`，MZ 是根目录直接有
`data/System.json`（没有 www 层），VX Ace 是有 `Data/Actors.rvdata2` 或 `Game.rvproj2`。
GUI 侧拖入文件夹后依次调用所有已注册 adapter 的 `detect()`，命中哪个就用哪个，都不命中就
在界面上明确提示"未识别到支持的引擎"，不要静默失败。

---

## 5. 核心数据模型

```python
class TextUnit(BaseModel):
    id: str                     # hash(engine + file_path + locator)，唯一
    engine: Literal["mv", "mz", "vxace"]
    file_path: str              # 相对工程根目录的路径
    locator: str                # 定位信息：json pointer 或 marshal 路径，用于回填时精确定位
    context: str                # 周边上下文（同一事件里前后的文本、说话人名等），喂给 LLM 用
    source_text: str            # 原文（已做控制码占位符替换）
    control_code_map: dict[str, str]  # 占位符 -> 原始控制码 的映射
    translated_text: str | None = None
    status: Literal["pending", "translated", "reviewed"] = "pending"
```

**去重/翻译记忆策略**（比 MTool 更精细的地方，用来缓解它"全局字典导致同词不同义被错误
复用"的问题）：

- 批量翻译时按 `source_text` 分组，相同原文默认只调用一次 LLM，结果写入翻译记忆表
  （key = `source_text` 的 hash），其余相同原文的 TextUnit 复用这个结果，兼顾效率和一致性。
- QA 校验阶段（`qa.py`）单独跑一遍：把"同一 source_text 出现在语义明显不同的 context 里"
  的情况标记出来，导出成一份待复核列表，不要试图全自动解决。

---

## 6. 引擎技术细节（已验证）

### 6.1 RPG Maker MV / MZ

**目录结构**：MV 是 `www/data/*.json`，MZ 是 `data/*.json`（少了一层 www）。

**需要遍历的文件**：`MapXXX.json`、`CommonEvents.json`，以及数据库文件
`Actors.json / Classes.json / Skills.json / Items.json / Weapons.json / Armors.json /
Enemies.json / States.json / Troops.json / System.json / MapInfos.json`。

**事件指令编码（决定从 Map/CommonEvents 里抓哪些字段，不要暴力抓所有字符串）**：
已确认的核心几个：
- `101`（Show Text 头部：脸图/背景/位置/说话人，说话人字段 MZ 独有）+ `401`（Show Text
  正文，逐行一条指令，`parameters[0]` 是文本）
- `102`（Show Choices，`parameters[0]` 是选项文本数组）
- `105` + `405`（Show Scrolling Text 头部与正文，结构同 101/401）
- `320`（Change Name，改角色显示名）
- `324` / `325`（MZ 新增：Change Nickname / Change Profile）
- `108` / `408`（Comment）：**默认跳过**，很多插件用注释存配置参数
- `355` / `655`（Script）：**默认跳过**，是 JS 代码，硬翻会把游戏跑崩

⚠️ 这份编码表来自社区文档整理，不是官方规范。实现时务必用一个已知不加密的 MV/MZ 样例
工程实际跑一遍解析，跟游戏画面里出现的文本人工核对，把编码表校准准确。有条件的话对照
RPG Maker corescript（`rmmz_objects.js` 里 `Game_Interpreter` 的 `command101`/`command401`
等方法）确认参数位置，不要直接假设本文档的表是完整精确的。

**数据库文件字段白名单**（2026-07-17 与用户确认后扩展）：每个数据库 JSON 是数组，元素里抓
`name`、`nickname`、`description`、`profile`（角色简介，Actors 特有）、`note`（note 混杂
插件配置和剧情备注，先抓出来，翻译前过一遍启发式过滤——整段是 `<tag:value>` 格式就跳过）、
`message1`~`message4`（Skills 战斗提示语）。字段按 key 是否存在于记录里判断，不按文件名
特判，多数文件本来就没有 `nickname`/`profile` 这些键。

**加密**（仅 `System.json` 里 `hasEncryptedImages`/`hasEncryptedMusic` 为 true 时才需要）：
`.rpgmvp/.rpgmvo/.rpgmvm` 文件前 16 字节是固定头，真正内容前 16 字节用密钥异或过，密钥是
`System.json` 里 `encryptionKey` 字段转成的字节数组，其余部分不加密。这个加密**只影响
图片/音频资源，不影响文本 JSON**，v1 先不实现，只在检测到用户明确要处理立绘文字（OCR
范畴，不在本工具范围）时再做 `mv_crypto.py`。

### 6.2 RPG Maker VX Ace

**格式**：`Data/*.rvdata2` 是 Ruby Marshal 4.8 版本序列化的二进制，用 `rubymarshal` 库
`load()` 读出嵌套对象。`Scripts.rvdata2` 特殊：是数组，每个元素是
`[id, 脚本名, zlib压缩后的脚本正文]`，正文要 `zlib.decompress` 才能拿到 Ruby 源码文本。

**需要遍历的文件**：`Data/Actors.rvdata2`、`Classes.rvdata2`、`Skills.rvdata2`、
`Items.rvdata2`、`Weapons.rvdata2`、`Armors.rvdata2`、`Enemies.rvdata2`、`States.rvdata2`、
`CommonEvents.rvdata2`、`MapXXX.rvdata2`、`System.rvdata2`。

**事件指令**：结构和 MV 高度相似（都是 `code + parameters`），社区反馈"大部分编码一致"，
但不要直接照搬 MV 的表，实现时同样要用真实工程核对。

**Scripts.rvdata2 里的文本**：v1 不翻译脚本正文，只在需要"给老引擎打自动换行补丁"时（见
第 9 节）写入一小段自己准备好的 Ruby 代码，不翻译已有脚本内容。

**类型注册风险**：RPG Maker 用了大量自定义 Ruby 类（`RPG::Map`、`RPG::EventCommand` 等），
`rubymarshal` 可能不认识这些类名。这是 M4 第一个要解决的技术验证项：写最小复现脚本读一个
真实 `Actors.rvdata2`，确认能不能正常解析，不能的话参考它文档的自定义类型注册机制补上。

### 6.3 RPG Maker XP / VX（v1 追加）

**格式**：和 VX Ace 一样是 Ruby Marshal 二进制（`.rxdata` for XP，`.rvdata` for VX，注意
后缀比 VX Ace 的 `.rvdata2` 少个 `2`），复用 M4 打好的 `rvdata2_codec.py` / rubymarshal
基础设施，但**不能假设类定义通用**：XP/VX 时代的 `RPG::xxx` 类字段和 VX Ace 不完全一致
（VX Ace 在 VX 基础上重构过数据结构），M4.5 的第一个任务就是照搬 M4 的验证方法——拿一份
真实 XP 或 VX 工程的 `Actors.rxdata`/`.rvdata` 实际读一遍，确认字段对不对，不能直接复用
VX Ace 的类注册表。

**编码**：比 VX Ace 更老，字符串编码可能是 Shift-JIS 而非 UTF-8，`rubymarshal` 读出来的
字符串需要显式做编码检测/转换，不能假设和 VX Ace 一样是 UTF-8——这是 M4.5 需要验证的
第二个风险点。

**事件指令/文本溢出处理**：结构预期和 VX Ace 高度相似（同样 4 行消息框、不自动换行），
第 9 节的换行方案原则上可以直接复用，但同样要用真实工程核对，不假设。

### 6.4 WOLF RPG エディター（v1 追加，高不确定性）

**这是本项目里唯一的非 RPG Maker 引擎**，格式完全没有官方文档，实现前必须先做一轮独立的
格式研究，不能照抄本节——本节内容本身就是通过调研社区工具间接得出的二手信息，务必在
动手写 `wolf_binary.py` 之前重新核实。

**已知信息（截至文档编写时）**：
- 工程由 `Data.wolf`（数据库定义 + 通用事件等）和按地图拆分的 `.mps` 文件（对应 RPG Maker
  的 MapXXX.json）组成，具体命名和目录布局以实际打开一个真实 WOLF 工程为准。
- 字符串编码：2.2 版本之前是 `cp932`（日文 Windows Shift-JIS 变种），2.2 之后支持 UTF-8，
  **实现时必须先探测版本号再决定解码方式**，不能写死一种编码。
- 社区已有三个独立实现证明格式可破解，均可作为移植参考：
  - `elizagamedev/wolftrans`（Ruby，MPL-2.0，最早的实现，被其余两个引用为"原始实现"）
  - `Sinflower/WolfTL`（C++，README 自述"解析逻辑基于 Wolf Trans"）
  - `KCFindstr/rewolf-trans`（TypeScript，受 Wolf Trans 和 Translator++ 启发）
  - 三者都**没有独立的格式规范文档**，理解格式只能靠读源码，本项目要移植成 Python 也一样。
- `wolftrans` 是 MPL-2.0 协议：可以参考/移植其解析逻辑，但如果直接复用/修改它的源文件，
  需要遵守 MPL-2.0 对"修改后的该文件"的开源披露要求——实现时如果确实照抄了它的文件级
  逻辑，要在 `wolf_binary.py` 头部标注来源和协议，不要不声明来源直接照搬。

**M4.8 的任务边界**（研究先行，不是直接写适配器）：
1. 用真实 WOLF 工程（自己找一个免费同人游戏样例，或用官方示例工程）+ 上面三个参考实现
   之一，验证能不能在 Python 里把 `.dat`/`.mps` 读出结构化数据。
2. 只有这一步验证通过，才进入正式实现 `EngineAdapter`（extract/inject）。
3. 如果调研后发现格式复杂度/工作量明显超出预期（比如涉及不透明的加密层、社区工具都
   依赖某个没法移植的运行时组件），**如实汇报工作量评估，不要为了"完成任务"硬着头皮
   糊一个不可靠的实现**——这类风险判断参考第 14 节的风险披露原则。

---

## 7. 控制码保护机制

RPG Maker 文本里嵌入控制码：`\C[n]`（变色）、`\N[n]`（角色名变量）、`\V[n]`（数值变量）、
`\P[n]`（头像）、`\I[n]`（图标）、`\G`（货币单位）、`\!`（等待输入）、`\.`/`\|`（暂停）、
`\^`（立即结束）等。送给 DeepSeek 前必须先转义成不透明占位符，翻译后再还原。

```python
CONTROL_CODE_PATTERN = re.compile(r"\\[A-Za-z]+(\[[^\]]*\])?")

def protect(text: str) -> tuple[str, dict[str, str]]:
    mapping = {}
    def repl(m):
        token = f"⟦CC{len(mapping)}⟧"
        mapping[token] = m.group(0)
        return token
    return CONTROL_CODE_PATTERN.sub(repl, text), mapping

def restore(text: str, mapping: dict[str, str]) -> str:
    for token, code in mapping.items():
        text = text.replace(token, code)
    return text
```

占位符要选一个几乎不可能被模型意外改写的形式，翻译 prompt 里显式声明
"`⟦CCn⟧` 是不可翻译、不可移动位置的占位符，原样保留"，实测时留意模型是否擅自增删占位符
两侧空格。

---

## 8. 翻译编排层设计

调用顺序：`extract` → 按 `source_text` 去重分组 → `protect` → 分批调用 DeepSeek（带
context 字段和术语表）→ `restore` → 写回 TextUnit → QA 一致性扫描 → `inject`。

**术语表（glossary）**：v1 先跑一遍全量文本用 DeepSeek 抽取人名/地名/专有名词候选列表，
GUI 上给一个简单表格允许用户确认/修改，存成 `term -> 固定译名`。后续所有翻译请求的
system prompt 里带上这张表，强制模型对这些词使用统一译名。

**批次划分**：同一事件里连续的 Show Text 行放同一批请求保持对话上下文连贯；数据库字段
（技能描述、道具说明）按文件分批。量大时并发（`httpx.AsyncClient` + 信号量限流，并发数
在 GUI 设置面板可调）。

---

## 9. 文本溢出/换行处理

- **MV/MZ**：消息框动态自动换行，译文变长基本不用特殊处理，正常写回 `\n` 保留原有人工
  换行点即可。
- **VX Ace**：消息框默认最多显示 4 行且**不会自动换行**，原文换行是作者手动按字符数掐出
  来的，翻译后长度对不上会溢出。处理方案：
  1. 提取时把同一条 Show Text 指令下的多行合并成完整段落再送翻译，不要逐行翻译。
  2. 回填时两个方案都要实现：
     a) 简单方案——按预估宽度重新分行（中文字符按 2 倍宽度估算，英文按 1 倍），塞回
        原有 4 行结构。v1 先做这个。
     b) 更稳方案——在目标工程的 `Scripts.rvdata2` 里注入一小段自定义 Ruby 补丁，重写
        `Window_Message` 绘制逻辑使其运行时按像素宽度动态换行（老引擎汉化圈常见做法）。
        留到 M5 有真实游戏可以跑测试时再做，需要针对具体引擎版本调试。

---

## 10. GUI 设计

主窗口（`main_window.py`）：
- **拖拽区域**：接受拖入游戏文件夹或 `Game.exe`（拖 exe 时自动定位到其所在目录）。拖入后
  立刻跑一遍所有 adapter 的 `detect()`，命中后显示识别出的引擎类型、粗略扫描出的文本条数
  估计；都不命中就明确提示"未识别到支持的 RPG Maker 引擎"。
- **设置入口**（`settings_dialog.py`）：DeepSeek API Key 输入框（通过 `keyring` 存取，界面
  上显示成掩码）、模型下拉框（`deepseek-v4-flash` 默认 / `deepseek-v4-pro`）、并发数滑块、
  输出目录选择。
- **主操作**：一个"开始汉化"按钮，点击后禁用自身防止重复触发，下方进度条 + 实时日志文本框
  （显示阶段：提取中 / 术语抽取中 / 翻译批次 X/Y / 写回中），完成后弹出提示并提供"打开输出
  文件夹"按钮。
- **术语表窗口**（`glossary_dialog.py`，v1 做最简单的版本）：翻译前弹出一个可编辑表格，
  展示自动抽取的专有名词候选和 DeepSeek 给的建议译名，用户可以直接改，确认后才继续走
  批量翻译。

**线程模型**：提取/翻译/写回这些耗时操作必须放进 `QThread`（`workers.py`），不能阻塞 GUI
主线程；后台线程通过 Qt Signal（比如 `progress = Signal(int, str)`）把进度和日志传回主
线程更新控件，**不要跨线程直接操作 UI 控件**。`pipeline.py` 内部如果用 `asyncio` 做并发
翻译请求，在 QThread 的 `run()` 方法里用 `asyncio.run(...)` 跑，不要尝试把 asyncio 事件循环
和 Qt 事件循环强行合并（除非后续确定要用 `qasync`，v1 先用简单方案）。

**配置持久化**：非敏感配置（模型选择、并发数、上次输出目录）用 `QSettings`；API Key 单独
走 `keyring`，绝不落地明文文件。

---

## 11. CLI（次要接口，开发调试用）

```
toolname extract  <project_dir> --out units.db
toolname glossary  --db units.db
toolname translate  --db units.db --concurrency 8
toolname qa          --db units.db --export conflicts.csv
toolname inject      --db units.db --project <project_dir> --out <output_dir>
toolname run          <project_dir> --out <output_dir>
```

这套 CLI 主要是给你自己开发时快速验证 `core/`、`engines/`、`translate/` 这几层逻辑用的，
不需要打包给最终用户，GUI 才是面向用户的主入口。

---

## 12. 打包与分发

- 用 **PyInstaller** 打包，先用 `--onedir` 模式（比 `--onefile` 启动快、更不容易被杀毒
  软件误报），稳定后再考虑要不要切 `--onefile`。
- 必须在一台没装 Python 的干净 Windows 环境（虚拟机即可）里实测双击直接能不能跑起来，
  不要只在开发机上测。
- PyInstaller 打包出来的 exe 经常被 Windows Defender / 国内杀毒软件误报为可疑程序，这是
  已知的普遍现象，不代表代码有问题；后续如果要缓解可以考虑代码签名，v1 先不做，记一笔
  在已知问题里，免得后面自己以为是代码 bug 排查半天。

---

## 13. 里程碑（按顺序实现；自 M0 完成起改为自测通过即自主推进，见第 16 节）

- **M0**：项目脚手架、`pyproject.toml`、`TextUnit` 模型、SQLite 存取层、CLI 骨架。
- **M1**：MV/MZ `extract` + `inject`，先不接翻译，做**原文回填自检**——提取后原样不翻译
  直接 inject，跟原工程逐字节 diff `data/*.json`，必须完全一致或只有预期内的格式差异，
  这是验证解析定位没有偏移的硬性关卡。
- **M2**：控制码保护 + DeepSeek 翻译编排（`llm_client.py`）+ 术语表，在一个小型真实 MV/MZ
  工程上跑通完整链路，人工检查游戏内实际显示效果。
- **M3**：QA 一致性扫描 + 断点续跑 + 并发限流，补齐 CLI 各子命令。
- **M3.5**：GUI MVP——拖拽识别、设置面板（API Key/模型/并发/输出目录）、开始按钮、进度条、
  日志区，接入 M0-M3 已经跑通的 MV/MZ 全流程。做到这一步应该已经是一个能日常使用的
  "MTool 式" MV/MZ 汉化工具了。
- **M4**：VX Ace adapter（先解决 `rubymarshal` 自定义类型解析的验证问题），复用 M0-M3 的
  翻译编排层。
- **M4.5（v1 追加）**：RPG Maker XP/VX adapter，见 6.3 节。先验证 `RPG::xxx` 类定义差异和
  字符串编码（可能是 Shift-JIS），复用 M4 的 rubymarshal 基础设施，不能假设和 VX Ace 通用。
- **M4.8（v1 追加，高不确定性）**：WOLF RPG エディター adapter，见 6.4 节。**先做格式研究
  验证，不是直接写适配器**——读 `wolftrans`/`WolfTL`/`rewolf-trans` 源码 + 真实 WOLF 工程
  样例，确认能否用 Python 读出 `.dat`/`.mps` 结构化数据，验证通过再实现
  extract/inject；如果调研后发现工作量远超预期，如实汇报评估，不强行交付。
- **M5**：VX Ace 换行处理，(a) 预估换行和 (b) 消息框补丁注入两条路径的验证；GUI 侧确认
  VX Ace 工程走全流程没问题（理论上不用改 GUI 代码，因为 GUI 只依赖 adapter 注册表）。
- **M6**：打包验证，PyInstaller 产出、干净环境实测、图标/窗口标题等收尾细节。
- **M7（之后再说）**：术语表更完善的审核界面、可选的运行时 hook 模式、RPG Maker
  2000/2003、WOLF 之外的其他非 RPG Maker 引擎。

---

## 14. 已知风险清单

- 少数游戏会把整个 `www` 目录打成 asar 包或用第三方保护工具混淆，`data/*.json` 不是直接
  明文可读——`detect()` 要能识别这种情况并给出明确报错，而不是静默解析出空结果。
- `note` 字段和注释指令里插件配置和真实文本混杂，需要持续维护"看起来像插件标签就跳过"
  的启发式规则。
- 同一 `source_text` 在不同语境下需要不同译法的情况，翻译记忆去重策略要能被人工覆盖
  （给 TextUnit 提供"不复用记忆，单独翻译"的标记位）。
- DeepSeek 模型名称/计费策略在 2026-07-24 有一次已知变更（见第 3 节），实现前务必去官方
  文档确认当前状态，不要完全信本文档写的字符串。
- PyInstaller 打包的 exe 被杀毒软件误报是已知现象，不是 bug。
- 分发层面：工具产出应该是用户在自己合法拥有的游戏上生成的汉化版本，不要做"从网上抓
  游戏本体"这类功能。

---

## 15. 验收标准（每个里程碑对照检查）

- M1：抽取-不翻译-回填后的工程能正常启动游戏，画面文本与原版逐字一致。
- M2：抽取-翻译-回填后的工程能正常启动，抽样对话框显示翻译结果且控制码正常生效，无
  `⟦CCn⟧` 占位符残留在实际显示文本里。
- M3：中途 kill 掉翻译进程重新执行，已翻译部分不重复调用 API。
- M3.5：在干净开发机上（不用命令行）从拖入游戏文件夹到拿到汉化版输出目录，全程只用鼠标。
- M4：`rubymarshal` 能完整读出一个真实 VX Ace 工程的全部数据库文件不报错。
- M4.5：`rubymarshal` 能完整读出一个真实 XP 或 VX 工程的全部数据库文件不报错，字符串
  编码（Shift-JIS/UTF-8）正确，抽取-不翻译-回填自检和 M1 一样跟原工程逐字节 diff 一致。
- M4.8：Python 能正确解析一个真实 WOLF 工程的 `.dat`/`.mps` 并提取出可读文本；如果研究
  阶段判定不可行，验收标准改为"交付一份可行性评估报告"，不强行要求功能完整。
- M5：抽样一个存在长对话的 VX Ace 游戏，回填后译文在游戏内不溢出消息框。
- M6：在没装 Python 的干净 Windows 虚拟机上双击 exe 能直接打开并完成一次完整汉化流程。

---

## 16. 变更记录

- **2026-07-17**：M0 完成后，用户明确要求不再逐里程碑暂停等待人工验收，改为"自测通过即
  自主推进"，仅在关键节点做简短进度播报；本文档第 1 节"M0-M3 完成后暂停等待人工验收"
  的表述已被此后续口头指示覆盖。
- **2026-07-17**：用户要求把 RPG Maker XP/VX 和 WOLF RPG エディター（ウディタ）纳入 v1
  范围，明确排除更老的 RPG Maker 2000/2003 和 WOLF 之外的其他非 RPG Maker 引擎；对应改动
  见第 2、4、6.3、6.4、13、15 节，新增里程碑 M4.5 / M4.8。WOLF 因为没有官方格式文档，
  M4.8 定位为"先研究可行性，再决定是否/如何实现"，不是直接承诺完整适配。

现在开始，从 M0 开始搭建。
