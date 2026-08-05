# resources/unity_mod/ 来源记录

本目录下四个变体目录（`mono_x86`/`mono_x64`/`il2cpp_x86`/`il2cpp_x64`）是把 BepInEx
（.NET mod 加载器）解压后，再把对应版本的 XUnity.AutoTranslator 插件包合并解压进去
（覆盖式合并 `BepInEx/` 子目录，不是整体替换）得到的。全部是从 GitHub Releases 手动
下载的第三方预编译二进制，**不进 git 仓库**，仅作为本地工作树素材。

下载时间：2026-08-04 14:26 (UTC+8)　／　2026-08-04 06:26 UTC

sha256 用 `d:/python312/python.exe -c "import hashlib; ..."`（分块读取整个文件）计算，
另外用 `sha256sum` 交叉核对过一遍，两者一致。

## mono_x64

- BepInEx（Mono，稳定版）：
  `https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_win_x64_5.4.23.5.zip`
  版本 `v5.4.23.5`
  sha256 `82f9878551030f54657792c0740d9d51a09500eeae1fba21106b0c441e6732c4`
- XUnity.AutoTranslator（BepInEx / Mono）：
  `https://github.com/bbepis/XUnity.AutoTranslator/releases/download/v5.6.1/XUnity.AutoTranslator-BepInEx-5.6.1.zip`
  版本 `v5.6.1`
  sha256 `fbb7d1bbe2c7cc168da6dccbc500fb74786a85a548f52495c8a1592ac46407f5`

## mono_x86

- BepInEx（Mono，稳定版）：
  `https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_win_x86_5.4.23.5.zip`
  版本 `v5.4.23.5`
  sha256 `37651c79e40d6f909572a4f461ac25350bb3ef8fe7fbd29f1aa8791a33b84c82`
- XUnity.AutoTranslator（BepInEx / Mono，同 mono_x64 那份，插件包不区分 x86/x64）：
  `https://github.com/bbepis/XUnity.AutoTranslator/releases/download/v5.6.1/XUnity.AutoTranslator-BepInEx-5.6.1.zip`
  版本 `v5.6.1`
  sha256 `fbb7d1bbe2c7cc168da6dccbc500fb74786a85a548f52495c8a1592ac46407f5`

## il2cpp_x64

- BepInEx（IL2CPP，pre-release，v5 稳定版不支持 IL2CPP，这是已知限制）：
  `https://github.com/BepInEx/BepInEx/releases/download/v6.0.0-pre.2/BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip`
  版本 `v6.0.0-pre.2`
  sha256 `616ec7eb06cf11b2a0000e8fcef04d1b12bb58e84a2e0bdac9523234fc193ceb`
- XUnity.AutoTranslator（BepInEx / IL2CPP）：
  `https://github.com/bbepis/XUnity.AutoTranslator/releases/download/v5.6.1/XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip`
  版本 `v5.6.1`
  sha256 `9d6b26e9d4957459bdb64b6d4852edb39cd5e8d31c28e0a157cefd6510ada811`

## il2cpp_x86

- BepInEx（IL2CPP，pre-release）：
  `https://github.com/BepInEx/BepInEx/releases/download/v6.0.0-pre.2/BepInEx-Unity.IL2CPP-win-x86-6.0.0-pre.2.zip`
  版本 `v6.0.0-pre.2`
  sha256 `cfef3a1e946dac5db8b9de4de1a922f47584dd775da32863f36762fbaad80f19`
- XUnity.AutoTranslator（BepInEx / IL2CPP，同 il2cpp_x64 那份，插件包不区分 x86/x64）：
  `https://github.com/bbepis/XUnity.AutoTranslator/releases/download/v5.6.1/XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip`
  版本 `v5.6.1`
  sha256 `9d6b26e9d4957459bdb64b6d4852edb39cd5e8d31c28e0a157cefd6510ada811`

## 合并方式

1. 先把对应 BepInEx zip 完整解压到目标目录根下（`unzip -o BepInEx_xxx.zip -d <目标目录>`）。
2. 再把对应 XUnity zip 解压到同一目标目录（`unzip -o XUnity.xxx.zip -d <目标目录>`），
   会把 `BepInEx/core/XUnity.Common.dll`、`BepInEx/plugins/XUnity.AutoTranslator/...`、
   `BepInEx/plugins/XUnity.ResourceRedirector/...` 合并进已经解压好的 `BepInEx/`
   子目录，不影响已存在的 `BepInEx/core/` 下 BepInEx 自带的其他 dll。
3. Mono 版 BepInEx zip 本身不带 `BepInEx/plugins/`、`BepInEx/patchers/` 空目录，是
   XUnity zip 解压时顺带建出来的；IL2CPP 版 BepInEx zip 自带这两个空目录。

## 原始下载文件缓存

6 个原始 zip 连同上面的 sha256 一起留在 `resources/unity_mod/_downloads/` 下，避免
维护者后续要重新核对/重新解压时还要再翻墙下一遍。这个子目录本身不是最终产物，只是
下载缓存，可以随时删掉重新拉。

## 已知的坑 / 后续写部署代码时要注意的点

- **没有任何一个 zip 自带 `AutoTranslatorConfig.ini`**——四个目标目录里都没有
  `BepInEx/config/` 目录。XUnity 插件自带的 README（`BepInEx/plugins/README
  (AutoTranslator).md`）明确写的是"配置文件在游戏启动时自动生成"（"The
  configuration file is created when the game is launched"），但 README 里
  BepInEx 安装方式那节给出的文件结构清单只列到
  `{GameDirectory}/BepInEx/Translation/AnyTranslationFile.txt`，**没有显式写清楚
  ini 配置文件的具体相对路径**（对比 UnityInjector 安装方式那节倒是显式写了
  `UnityInjector/Config/Translation/...`）。社区里普遍认为 BepInEx 安装下配置文件
  落在 `BepInEx/config/AutoTranslatorConfig.ini`，但这份 README 本身没有明确背书这
  一点，建议实际拿一个 Unity 游戏跑一遍确认，而不是直接按这个记忆走。
- Mono 和 IL2CPP 两条线的 `doorstop_config.ini` 里 `target_assembly` 不同：
  - Mono：`target_assembly=BepInEx\core\BepInEx.Preloader.dll`
  - IL2CPP：`target_assembly = BepInEx\core\BepInEx.Unity.IL2CPP.dll`，另外多了
    `[Il2Cpp]` 段，`coreclr_path = dotnet\coreclr.dll`、`corlib_dir = dotnet`——
    IL2CPP 版必须连 `dotnet\` 整个目录一起复制过去，缺了它直接起不来。
- IL2CPP 版体积明显更大（因为要自带一份 .NET 运行时到 `dotnet/` 目录下，231 个文件
  75MB+ 解压后），Mono 版只有 22 个文件、不到 2MB，这个体积差异在设计下载/打包流程
  （比如要不要分开测试 mono/il2cpp 两种下载路径、要不要给 IL2CPP 包单独提示用户
  "这个包更大"）时应该考虑到。
- XUnity 插件包里除了 `XUnity.AutoTranslator/` 子目录，还额外带了一个
  `BepInEx/plugins/XUnity.ResourceRedirector/` 子目录（`XUnity.ResourceRedirector.dll`
  + `XUnity.ResourceRedirector.BepInEx(-IL2CPP).dll`），这是 XUnity 生态里另一个独立
  插件（资源重定向，用于贴图/字体替换等），随 AutoTranslator 一起发布。如果部署代码
  要"只要翻译功能"而不需要这个，需要显式过滤掉这个子目录，不能假设 XUnity 包解压出来
  只有 AutoTranslator 一个东西。
- BepInEx IL2CPP 目前只有 pre-release（v6.0.0-pre.2），版本号带 `-pre.2` 后缀，不是
  正式稳定版——这是已知限制，README 里 IL2CPP 那节原文也提到"as of this writing are
  only available as bleeding edge builds"。
