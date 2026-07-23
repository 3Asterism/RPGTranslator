# RPG Translator

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-PySide6-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)

**RPG Maker / WOLF RPG エディター 游戏文本提取 → DeepSeek 翻译 → 回填工具。**
把游戏文件夹拖进窗口，一键提取文本、调用 DeepSeek API 翻译、原地写回游戏工程本身——
注入前自动备份原文版本，随时能用「切换为原文 / 切换为译文」中日对照，不用另外拷贝一份
汉化目录。

体验类似 MTool，但翻译记忆更细：相同原文默认复用同一个译名，QA 阶段会单独把"同一句话
在不同语境下可能需要不同译法"的情况挑出来，而不是无脑全局替换。

---

## 支持引擎

| 引擎 | 状态 | 备注 |
|---|---|---|
| RPG Maker MV / MZ | ✅ 完整支持 | 明文 JSON，已用真实工程校准过事件指令编码表 |
| RPG Maker VX Ace | ✅ 完整支持 | Ruby Marshal 二进制，含消息框运行时像素级动态换行补丁（spec 9.2.b，见下方"已知局限"）；数据库/事件文本抽取已用真实工程验证过 |
| RPG Maker XP | ✅ 完整支持 | 已用真实 XP 工程（GitHub 上的 GPL-3.0 同人游戏 torresflo/Pokemon-Obsidian）验证并修复两个真机才暴露的 bug（见下方"已知局限"） |
| RPG Maker VX | ✅ 完整支持 | 和 XP 共用一套适配器代码；已用真实 VX 工程（GitHub 上的开源同人游戏 ambratolm-games/flower-in-pain）验证并修复一个 Ruby Marshal 写入库的对象引用 bug（见下方"已知局限"） |
| WOLF RPG エディター（ウディタ） | ✅ 完整支持 | 已用 WOLF RPG Editor 官方自带示例工程验证过（Map/CommonEvent/Database 三种文件全覆盖，含当前编辑器版本默认的 LZ4 压缩格式）；仍不支持 WolfPro 加密保护和经典 XOR 加密的工程 |
| RPG Maker 2000/2003 | ❌ 不支持 | 完全不同的格式，明确排除在范围外 |

**拖进来的是单文件 exe、没有散落的工程文件？** 不少 RPG Maker MV/MZ 游戏用
[Enigma Virtual Box](https://enigmaprotector.com/en/aboutvb.html) 把 `www` 资源目录和
nw.js 运行时整个打包进一个 exe 里分发（磁盘上找不到 `www/data`，只有孤零零一个几百 MB
到几 GB 的 exe）。拖进这类文件夹时，如果正常识别失败、又在顶层找到一个这样打包的 exe，
会自动解包到同级的 `<原目录名>_已解包` 目录（体积大的话要等一会），解包完自动重新识别
引擎——不挑具体是 MV/MZ 还是 VX Ace/XP/VX/WOLF，解包这一步和引擎种类无关，识别哪种引擎
交给解包完之后的正常识别流程。

## 功能

- **控制码保护**：`\C[n]` `\N[n]` `\V[n]` 等变量/颜色码翻译前转义成占位符，翻译后精确还原并校验完整性；
  `\n<角色名>正文` 这种说话人标记写法（部分工程的约定）会把角色名和正文拆开分别翻译再拼回去，模型全程
  看不到尖括号，从根上避免"该不该保留这段标记"的判断失误
- **翻译记忆去重**：相同原文只调用一次 API，兼顾效率和一致性
- **断点续传**：中途手动停止或进程被杀，重开软件继续跑，已翻译内容不会被打回重译
- **翻译失败自动重试**：单条翻译失败不拖累整批，失败条目保留待译状态；一轮翻译跑完后自动原地重试
  2 轮（间隔 5 秒），仍失败的可以点「重试失败项」按钮直接重跑（不用重新走一遍提取）
- **多 provider 故障转移**：主 provider 连续报错（限流/5xx）自动切换备用 provider，重试用指数退避
- **QA 一致性扫描**：同一原文在不同语境下可能需要不同译法的情况，单独导出成待复核列表
- **原文/译文一键切换**：翻译效果有问题时先切回原文核对，不用重新跑一遍注入
- **翻译包分享**：导出成一份轻量 `.rpgtrans.json`，同一版本游戏的其他人可以直接导入复用，不用重新花 API 额度
- **并发限流 + 批量打包请求**：省时间也省 token（配合 DeepSeek 的 prompt caching），批量大小可在设置面板调整
- **限流自适应退避**：撞到 429 时同一 provider 上所有并发请求共享一个冷却窗口（优先读 `Retry-After`，
  否则按连续命中次数指数退避），避免各自独立重试反复冲撞同一个限流窗口
- **单文件 exe 自动解包**：拖进来的游戏是 Enigma Virtual Box 打包的单文件 exe（找不到散落的
  `www/data`）时自动解包再识别，不用先手动找工具解包

## 快速开始

需要 Python 3.11+。

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

复制 `.env.example` 为 `.env` 填入 API Key（或者直接在 GUI 设置面板里填，走系统凭据管理器
存取，不落地明文文件）：

```
DEEPSEEK_API_KEY=你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

启动 GUI：

```bash
.venv\Scripts\rpg-translator-gui.exe
```

**流程**：拖入游戏文件夹（或 `Game.exe`）→ 自动识别引擎 → 点「开始翻译」→ 翻译跑在后台，
随时可以点「停止」；有条目翻译失败会自动重试几轮，还失败的可以点「重试失败项」再跑一次 →
点「注入到游戏」原地写回游戏工程（注入前自动备份原文版本）→ 用「切换为
原文 / 切换为译文」中日对照，或者「导出翻译包」分享给同游戏的其他人。API Key 等设置在窗口
右上角的「⚙ 设置」按钮里。

已打包好的 Windows 版本见 [Releases](../../releases)。

## 配置翻译引擎：在线 API / 本地模型

设置面板（右上角「⚙ 设置」）里第一项「翻译引擎」可以在两者之间切换，选中哪个就用哪个，
互不干扰、可以随时切回去，各自的配置分开存（在线走 `.env`/系统凭据管理器，本地模型同理）。

### 在线（云端 API，默认）

适合没有独立显卡、或者不想占用本机资源的场景。默认接 DeepSeek，也兼容任何 OpenAI
`/v1/chat/completions` 协议的服务商（阿里云百炼、SiliconFlow 等）。

在设置面板「在线 Provider」里填：
- **API Key**：走系统凭据管理器（Windows 凭据管理器 / keyring），不落地明文文件
- **Base URL**：留空默认 `https://api.deepseek.com`，换其他兼容服务商就填对应地址
- **模型**：下拉选或者手填（比如切换到更便宜/更贵的档位）

也可以不开 GUI，直接在项目根目录 `.env` 里配置（复制 `.env.example`）：

```
DEEPSEEK_API_KEY=你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

「备用 Provider」（可选）：主 provider 连续报错（限流/5xx）时自动切过去，三个字段都留空
就是不启用，行为和以前一样。

### 本地模型（比如 Ollama 部署的 Sakura）

适合有独立显卡（实测 12GB 显存能跑得动 7B 量化模型）、想完全离线翻译、或者不想为翻译付
API 费用的场景。走的是专门适配过 [SakuraLLM/GalTransl](https://github.com/SakuraLLM/SakuraLLM)
系列模型的 prompt 模板（见 `translate/sakura_prompt.py`），不是把在线那套 prompt 直接
糊给本地模型——这类小模型是照着固定模板微调的，喂别的格式效果会打折扣。

部署步骤（以 Ollama 为例，Windows/Linux 通用）：

1. 装 [Ollama](https://ollama.com/download)
2. 下载一个 GalTransl 系列的 GGUF 权重，比如
   [SakuraLLM/Sakura-GalTransl-7B-v3.7](https://huggingface.co/SakuraLLM/Sakura-GalTransl-7B-v3.7)
   （12GB 显存推荐 Q5_K_S/Q6_K 量化档位，显存小就用 IQ4_XS）
3. 写一个 `Modelfile`：
   ```
   FROM /path/to/sakura-galtransl-7b-v3.7-q5_k_s.gguf
   PARAMETER temperature 0.3
   PARAMETER top_p 0.8
   PARAMETER num_ctx 4096
   ```
4. `ollama create sakura-galtransl -f Modelfile`，然后 `ollama serve`（默认监听
   `127.0.0.1:11434`，同一局域网内其他机器要访问的话在启动 `ollama serve` 前设置环境变量
   `OLLAMA_HOST=0.0.0.0:11434`）

在设置面板里把「翻译引擎」切到「本地模型」，「本地 Provider」里填：
- **Base URL**：比如 `http://127.0.0.1:11434/v1`（同局域网的另一台机器就填它的内网 IP）
- **模型名**：`ollama create` 时起的名字，比如 `sakura-galtransl`
- **API Key**：一般留空即可，Ollama 默认不校验这个字段

已知局限：本地小模型批量打包翻译时偶尔会输出行数对不上（自动退化成逐条重试，不会丢译文，
只是变慢）；人名等专有名词的音译一致性不如在线大模型稳定（没有项目级术语表约束）。

## CLI（开发调试用，不是给最终用户的主入口）

```bash
rpg-translator extract   <项目目录> --out units.db
rpg-translator translate --db units.db --concurrency 8 --batch-size 50
rpg-translator qa        --db units.db --export conflicts.csv
rpg-translator inject    --db units.db --project <项目目录> --out <输出目录>
rpg-translator run       <项目目录> --out <输出目录>
```

## 测试

```bash
.venv\Scripts\pytest
```

部分测试会真实调用配置好的 LLM API，本地没配 `DEEPSEEK_API_KEY` 时自动跳过，不会失败。

## 打包

```bash
.venv\Scripts\python scripts\build.py
```

产出 `dist/RPGTranslator/`（PyInstaller `--onedir` 模式）。目前只在开发机上验证过启动，
还没在没装 Python 的干净 Windows 环境里实测过，分发前建议自行确认一遍。

## 已知局限

- **RGSS 引擎（VX Ace/XP/VX）共用的 Ruby Marshal 写入库有一个真机才暴露的对象引用 bug，已
  修复**：真实 XP/VX 工程（分别是 GitHub 上的 torresflo/Pokemon-Obsidian、
  ambratolm-games/flower-in-pain 两个开源同人游戏）实测发现，第三方 `rubymarshal` 库的
  `Writer.must_write` 只用 Python `id(obj)` 判断"这个对象是不是写过、该写反向引用"，既没有
  在 `str`/`bytes` 顶层字符串值上正确登记（只有 `RubyString` 会），也没有防住 CPython 内存
  地址复用——两个问题叠加，实测下来 8 个真实地图文件里有 3 个回填后连自己都读不回来（或者更
  隐蔽地静默读出错误对象，不报错但数据是坏的）。已经在 `rvdata2_codec.py` 里包一层
  `_SafeWriter` 子类堵上，真实工程重新验证过没问题了，详见该文件顶部注释和
  `tests/test_rvdata2_codec.py` 的回归测试。
- **XP 专属的字符串编码 bug，已修复**：XP（和大概率 VX）用的老版本 Ruby（1.8，字符串没有
  编码感知）marshal 出来的字符串，`rubymarshal` 不会像 VX Ace（Ruby 1.9+）那样自动解码，原样
  是 `bytes`；旧代码对这类值直接调用 Python `str()`，抽出来的"文本"其实是 `b'...'` 这种
  repr 字面量，完全不能用，且写回时也不会正确编码回 bytes。已在 `_rgss_common.py` 加
  `rv_str`/`_encode_like`（先试 UTF-8 后退回 cp932）修复，真实 XP/VX 工程重新验证过。
- VX Ace 消息框运行时像素级动态换行补丁（spec 9.2.b）已实现并注入真实工程验证过：往
  `Scripts.rvdata2` 追加一段 `Window_Message#process_character` 的 monkey patch，按
  `contents.text_size` 量出的真实像素宽度决定换行点，复用引擎自带的翻页逻辑处理超过 4 行
  的情况；检测到已知第三方消息系统脚本（YEA/Galv/Luna/MOG 等关键词）会自动跳过、降级用估算
  重排方案兜底。已用真实 VX Ace 工程验证过补丁能正确注入、原有 100+ 个脚本条目逐字节不变、
  打上补丁的游戏能正常启动不报错；受限于当前开发环境截不到 DirectX 渲染内容的画面，还没能
  截图肉眼确认换行/翻页的实际视觉效果，这一步建议后续在能正常截图的机器上补做一次
- WOLF 格式没有官方文档，`wolf_binary.py` 已用 WOLF RPG Editor 官方自带示例工程验证过
  Map/CommonEvent/Database 三种文件（含当前编辑器版本默认的 LZ4 压缩格式、v3.5 版本的
  Page/Command 结构变化），仍明确不支持 WolfPro 加密保护和经典 XOR 加密的工程，遇到会直接
  报错而不是猜测/静默产出乱码
- PyInstaller 打包出的 exe 可能被杀毒软件误报，是已知的普遍现象
- 单文件 exe 自动解包目前只认 Enigma Virtual Box 这一种打包方式（`evbunpack`），像
  VMProtect/Themida 这类加壳保护、或者把资源塞进 NSIS 安装包里的分发方式不在覆盖范围内，
  遇到这些会照常回退到"未识别到支持的引擎"

WOLF 格式的逆向工程来源：[wolftrans](https://github.com/elizagamedev/wolftrans)、
[WolfTL](https://github.com/Sinflower/WolfTL)、
[rewolf-trans](https://github.com/KCFindstr/rewolf-trans) 三个社区项目的格式研究成果，
交叉验证后移植（详见 `engines/wolf_binary.py` 顶部注释）。

## 技术栈

Python 3.11+ · PySide6（GUI） · pydantic v2 · SQLite · httpx（异步） · rubymarshal ·
typer（CLI） · PyInstaller

## License

[MIT](LICENSE)
