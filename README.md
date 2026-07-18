# RPG Translator

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-PySide6-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)

**RPG Maker / WOLF RPG エディター 游戏文本提取 → DeepSeek 翻译 → 回填工具。**
把游戏文件夹拖进窗口，一键提取文本、调用 DeepSeek API 翻译、回填出一份可直接运行的
汉化版拷贝——**不改动原工程**，随时能切回原文对照。

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

## 功能

- **控制码保护**：`\C[n]` `\N[n]` `\V[n]` 等变量/颜色码翻译前转义成占位符，翻译后精确还原并校验完整性
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
点「注入到游戏」写回一份新的汉化拷贝，原工程不受影响 → 用「切换为
原文 / 切换为译文」中日对照，或者「导出翻译包」分享给同游戏的其他人。API Key 等设置在窗口
右上角的「⚙ 设置」按钮里。

已打包好的 Windows 版本见 [Releases](../../releases)。

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

完整技术规格、每个里程碑的验收标准、逆向工程来源（WOLF 部分参考了
[wolftrans](https://github.com/elizagamedev/wolftrans)、
[WolfTL](https://github.com/Sinflower/WolfTL)、
[rewolf-trans](https://github.com/KCFindstr/rewolf-trans) 三个社区项目的格式研究成果）都记录在
[`galgame_rpgmaker_translator_spec (1).md`](<galgame_rpgmaker_translator_spec (1).md>)。

## 技术栈

Python 3.11+ · PySide6（GUI） · pydantic v2 · SQLite · httpx（异步） · rubymarshal ·
typer（CLI） · PyInstaller

## License

[MIT](LICENSE)
