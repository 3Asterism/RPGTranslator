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
| RPG Maker VX Ace | ✅ 完整支持 | Ruby Marshal 二进制，含消息框自动重排版（4 行限制不溢出） |
| RPG Maker XP / VX | ✅ 完整支持 | 同族格式，字符串编码自动探测（Shift-JIS / UTF-8） |
| WOLF RPG エディター（ウディタ） | ⚠️ 研究向支持 | 无官方格式文档，基于社区逆向工程实现移植，未用真实工程验证过 |
| RPG Maker 2000/2003 | ❌ 不支持 | 完全不同的格式，明确排除在范围外 |

## 功能

- **控制码保护**：`\C[n]` `\N[n]` `\V[n]` 等变量/颜色码翻译前转义成占位符，翻译后精确还原
- **术语表**：自动抽取人名/地名候选，GUI 表格里确认/编辑后，后续所有翻译请求强制统一译名
- **翻译记忆去重**：相同原文只调用一次 API，兼顾效率和一致性
- **断点续传**：中途手动停止或进程被杀，重开软件继续跑，已翻译内容不会被打回重译
- **多 provider 故障转移**：主 provider 连续报错（限流/5xx）自动切换备用 provider，重试用指数退避
- **QA 一致性扫描**：同一原文在不同语境下可能需要不同译法的情况，单独导出成待复核列表
- **原文/译文一键切换**：翻译效果有问题时先切回原文核对，不用重新跑一遍注入
- **翻译包分享**：导出成一份轻量 `.rpgtrans.json`，同一版本游戏的其他人可以直接导入复用，不用重新花 API 额度
- **并发限流 + 批量打包请求**：省时间也省 token（配合 DeepSeek 的 prompt caching）

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

**流程**：拖入游戏文件夹（或 `Game.exe`）→ 自动识别引擎 → 点「开始翻译」→ 确认/编辑弹出的
术语表 → 翻译跑在后台，随时可以点「停止」→ 点「注入到游戏」写回一份新的汉化拷贝，原工程
不受影响 → 用「切换为原文 / 切换为译文」中日对照，或者「导出翻译包」分享给同游戏的其他人。

已打包好的 Windows 版本见 [Releases](../../releases)。

## CLI（开发调试用，不是给最终用户的主入口）

```bash
rpg-translator extract   <项目目录> --out units.db
rpg-translator glossary  --db units.db
rpg-translator translate --db units.db --concurrency 8
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

- WOLF / XP / VX 三个引擎的适配器目前只用手工构造的合成样例验证过，没有真实游戏工程实测
- VX Ace 长对话的换行方案目前只做了「按估算宽度重新分行」，运行时像素级动态换行的补丁方案
  还没做（需要真实游戏才能针对具体引擎版本调试）
- WOLF 格式没有官方文档，`wolf_binary.py` 明确不支持 WolfPro 加密保护和经典 XOR 加密的工程，
  遇到会直接报错而不是猜测/静默产出乱码
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
