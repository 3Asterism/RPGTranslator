# RPG Translator

RPG Maker 游戏文本提取 / DeepSeek 翻译 / 回填工具。把游戏文件夹拖进 GUI，一键提取文本、
调用 DeepSeek API 翻译、回填出一份可直接运行的汉化版拷贝——不改动原工程。

## 当前进度

- ✅ RPG Maker MV / MZ：提取（对话、选项、改名/改称号/改简介、数据库字段）、控制码保护、
  DeepSeek 翻译（术语表 + 翻译记忆去重 + 并发限流）、QA 一致性扫描、断点续跑、GUI。
- 🚧 计划中：RPG Maker VX Ace / XP / VX、WOLF RPG エディター（详见项目根目录的
  `galgame_rpgmaker_translator_spec (1).md` 里程碑列表）。

## 安装

需要 Python 3.11+。

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

## 配置 DeepSeek API Key

复制 `.env.example` 为 `.env`，填入你的 key（`.env` 已在 `.gitignore` 里，不会被提交）：

```
DEEPSEEK_API_KEY=你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

GUI 里也可以在"设置"面板直接填 API Key，会走系统凭据管理器（keyring）存取，不落地明文文件。

## 使用（GUI，面向最终用户的主入口）

```bash
.venv\Scripts\rpg-translator-gui.exe
```

把游戏文件夹（或 `Game.exe`）拖进窗口 → 识别引擎 → 点"开始汉化" → 确认/编辑弹出的术语表
→ 等翻译完成 → 点"打开输出文件夹"。

## 使用（CLI，开发调试用，不是给最终用户的）

```bash
rpg-translator extract  <项目目录> --out units.db
rpg-translator glossary --db units.db
rpg-translator translate --db units.db --concurrency 8
rpg-translator qa        --db units.db --export conflicts.csv
rpg-translator inject    --db units.db --project <项目目录> --out <输出目录>
rpg-translator run       <项目目录> --out <输出目录>
```

## 测试

```bash
.venv\Scripts\pytest
```

部分测试会真实调用配置好的 LLM API（本地没配 `DEEPSEEK_API_KEY` 时会自动跳过，不会失败）。

## 技术细节 / 设计决策

完整规格和里程碑记录在项目根目录的 `galgame_rpgmaker_translator_spec (1).md`，包括每次范围
变更的原因（第 16 节变更记录）。
