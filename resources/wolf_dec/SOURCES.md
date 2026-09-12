# resources/wolf_dec/ 来源记录

这个目录下的 `UberWolfCli.exe` 是从 GitHub Releases 下载的第三方预编译二进制
（跑 `scripts/fetch_wolf_dec.py` 得到），**不进 git 仓库**，仅作为本地工作树素材，
跟 `resources/unity_mod/`、`resources/fonts/` 同样的处理方式。

## 用途

WOLF RPG Editor（ウディタ）发布版游戏经常把整个 `Data/` 目录打包成单个
`Data.wolf`（DxLib 的 DXArchive 容器）。这个容器除了标准 DXArchive 格式外，
实测还叠加了一层目前没有任何公开资料（wolftrans/rewolf-trans/WolfTL/WolfDec 的
源码及内置密钥列表）记录过的额外混淆——文件头里定位实际数据的几个地址字段
用已知的固定密钥都解不开。继续纯 Python 逆向这层未知混淆算法，正确性没有把握、
时间成本也不可控，所以改为调用这个已经在社区里持续维护、经过真实游戏文件验证
能正确解包的工具做"解包 Data.wolf → 明文 Data/ 目录"这一步的后端，本项目自己
的 `engines/wolf.py`/`wolf_binary.py`（已验证能正确解析明文 `.dat`/`.mps`）接手
剩下的提取/翻译/写回。调用方式见 `engines/wolf_archive.py`。

## 来源

- 项目：[Sinflower/UberWolf](https://github.com/Sinflower/UberWolf)（MIT License）
- 版本：`v0.6.4`
- 下载 URL：
  `https://github.com/Sinflower/UberWolf/releases/download/v0.6.4/UberWolfCli.exe`
- sha256：`0c9645733ae9544df11ee0c859a7f2cb51aa547d5d13f7935cb480bdab96fb3a`
- 下载时间：2026-09-12

## 验证记录

针对真实游戏（WOLF RPG Editor 打包发行版，`Data.wolf` 约 533MB）实测跑通：
`UberWolfCli.exe -o "<游戏本体 exe>"` 能正确解包出完整的 `Data/BasicData/*.dat`、
`Data/MapData/*.mps`、图标 PNG 等，文件时间戳与内容均正常（PNG 能正确解码，
`.dat` 文件大小合理）。使用的具体解密方案是该游戏 `Data.wolf` 头部
`Flags >> 16 == 0x15E`（"Wolf RPG v3.50"）对应的密钥，这条信息由 UberWolf 自己
的开源实现（`WolfDec.cpp` 的 `DEFAULT_CRYPT_MODES` 表）驱动自动识别，不需要
本项目关心具体密钥值。
