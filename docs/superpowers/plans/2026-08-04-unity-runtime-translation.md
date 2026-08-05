# Unity 游戏运行时外挂翻译 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 拖拽识别 Unity 游戏工程后，自动判断 Mono/IL2CPP + x86/x64，部署对应的 BepInEx + XUnity.AutoTranslator 变体，把翻译请求路由到本地 shim 服务器转调用现有 `LLMClient`，实现侵入性最低（不改游戏文件本体、可完全卸载还原）的运行时外挂翻译。

**Architecture:** 新增平行的 `src/rpg_translator/unity/` 包（detect / placeholders / prompt / deploy / translate_shim 五个模块），不实现 `EngineAdapter` 接口，`pipeline.py` 现有函数不受影响。GUI 层在 `detect_adapter` 识别失败后追加 `detect_unity` 探测，命中则切到新的"部署/卸载"面板。BepInEx+XUnity 二进制素材由 `scripts/fetch_unity_mod_assets.py` 在构建期下载，不进 git。

**Tech Stack:** Python 3.11+、PySide6（GUI）、httpx（`LLMClient` 已用）、标准库 `http.server`/`socket`/`struct`/`zipfile`（不引入新的第三方依赖）。

## Global Constraints

- Windows only，跟现有项目平台范围一致，不用考虑 Linux/macOS 路径分隔符等问题。
- `open()` 一律显式 `encoding='utf-8'`。
- 新模块不依赖现有 `Store`/`units.db`——Unity 翻译路径全程无状态，翻译记忆交给 XUnity 自身缓存。
- 不复用 `codec/control_codes.py`（RPG Maker 专属转义语法）和 `translate/batch_translator.py` 的 `_TRANSLATE_SYSTEM_PROMPT`/`DEFAULT_BATCH_SIZE`（绑死 RPG Maker 控制码约定和批量结构）——`unity/` 包只复用 `translate/llm_client.py` 的 `LLMClient`/`LLMConfig`。
- BepInEx+XUnity 二进制素材不提交 git；`resources/unity_mod/` 加入 `.gitignore`。
- 详细背景/协议依据见 `docs/superpowers/specs/2026-08-04-unity-runtime-translation-design.md`，本计划的每个任务都对应该文档的一节，不重复解释"为什么"，只给"怎么做"。

---

## Task 1: Unity 工程探测（Mono/IL2CPP + x86/x64）

**Files:**
- Create: `src/rpg_translator/unity/__init__.py`
- Create: `src/rpg_translator/unity/detect.py`
- Test: `tests/test_unity_detect.py`

**Interfaces:**
- Produces:
  - `UnityTarget` (frozen dataclass): `exe_path: Path`, `data_dir: Path`, `backend: Literal["mono", "il2cpp"]`, `arch: Literal["x86", "x64"]`
  - `detect_unity(project_dir: Path) -> UnityTarget | None`
  - `InvalidPEFileError(ValueError)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_unity_detect.py
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rpg_translator.unity.detect import UnityTarget, detect_unity


def _pe_bytes(machine: int) -> bytes:
    """构造一个最小的、能通过 detect_unity 里 PE 头解析的假 exe 字节串：64 字节
    DOS 头（e_lfanew 指向紧随其后的 PE 头）+ 4 字节 PE 签名 + 2 字节 Machine 字段。
    detect_unity 只读前 70 字节，不需要更完整的 PE 结构。"""
    dos_header = bytearray(64)
    dos_header[0:2] = b"MZ"
    struct.pack_into("<I", dos_header, 0x3C, 64)
    pe_header = b"PE\x00\x00" + struct.pack("<H", machine) + b"\x00" * 18
    return bytes(dos_header) + pe_header


def _write_exe(path: Path, machine: int) -> None:
    path.write_bytes(_pe_bytes(machine))


def _make_mono_project(tmp_path: Path, machine: int = 0x8664) -> Path:
    exe = tmp_path / "Game.exe"
    _write_exe(exe, machine)
    data_dir = tmp_path / "Game_Data"
    (data_dir / "Managed").mkdir(parents=True)
    (data_dir / "globalgamemanagers").write_bytes(b"\x00")
    (data_dir / "Managed" / "Assembly-CSharp.dll").write_bytes(b"\x00")
    return tmp_path


def _make_il2cpp_project(tmp_path: Path, machine: int = 0x8664) -> Path:
    exe = tmp_path / "Game.exe"
    _write_exe(exe, machine)
    data_dir = tmp_path / "Game_Data"
    data_dir.mkdir(parents=True)
    (data_dir / "data.unity3d").write_bytes(b"\x00")
    (tmp_path / "GameAssembly.dll").write_bytes(b"\x00")
    return tmp_path


def test_detect_unity_mono_x64(tmp_path: Path):
    _make_mono_project(tmp_path, machine=0x8664)

    target = detect_unity(tmp_path)

    assert target is not None
    assert target.backend == "mono"
    assert target.arch == "x64"
    assert target.exe_path == tmp_path / "Game.exe"
    assert target.data_dir == tmp_path / "Game_Data"


def test_detect_unity_mono_x86(tmp_path: Path):
    _make_mono_project(tmp_path, machine=0x014C)

    target = detect_unity(tmp_path)

    assert target is not None
    assert target.arch == "x86"


def test_detect_unity_il2cpp_x64(tmp_path: Path):
    _make_il2cpp_project(tmp_path, machine=0x8664)

    target = detect_unity(tmp_path)

    assert target is not None
    assert target.backend == "il2cpp"
    assert target.arch == "x64"


def test_detect_unity_returns_none_for_non_unity_dir(tmp_path: Path):
    (tmp_path / "readme.txt").write_text("not a game", encoding="utf-8")

    assert detect_unity(tmp_path) is None


def test_detect_unity_returns_none_when_backend_unknown(tmp_path: Path):
    """有 Unity 目录结构特征，但既没有 GameAssembly.dll 也没有
    Managed/Assembly-CSharp.dll——判不出 Mono/IL2CPP，不瞎猜，返回 None。"""
    exe = tmp_path / "Game.exe"
    _write_exe(exe, 0x8664)
    data_dir = tmp_path / "Game_Data"
    data_dir.mkdir()
    (data_dir / "globalgamemanagers").write_bytes(b"\x00")

    assert detect_unity(tmp_path) is None


def test_detect_unity_skips_non_unity_exe_and_finds_real_one(tmp_path: Path):
    """目录下可能有多个 exe（比如 crash handler），第一个配不上 _Data 目录的
    要跳过，继续找下一个。"""
    crash_handler = tmp_path / "UnityCrashHandler64.exe"
    _write_exe(crash_handler, 0x8664)

    _make_mono_project(tmp_path)

    target = detect_unity(tmp_path)

    assert target is not None
    assert target.exe_path == tmp_path / "Game.exe"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_detect.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'rpg_translator.unity'`）

- [ ] **Step 3: Implement `unity/__init__.py` and `unity/detect.py`**

```python
# src/rpg_translator/unity/__init__.py
```
（空文件，只是把 `unity` 标记成包）

```python
# src/rpg_translator/unity/detect.py
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Backend = Literal["mono", "il2cpp"]
Arch = Literal["x86", "x64"]

_DATA_MARKER_NAMES = ("globalgamemanagers", "data.unity3d")


@dataclass(frozen=True)
class UnityTarget:
    exe_path: Path
    data_dir: Path
    backend: Backend
    arch: Arch


class InvalidPEFileError(ValueError):
    pass


def _find_unity_data_dir(project_dir: Path) -> tuple[Path, Path] | None:
    """找 <ExeName>.exe 同级的 <ExeName>_Data/ 目录，且其中含 globalgamemanagers
    或 data.unity3d 才算数（避免把同名普通文件夹误判）。目录下可能有多个 exe
    （比如自带的 UnityCrashHandler64.exe），逐个试，第一个配对成功的即为主程序。"""
    for exe_path in sorted(project_dir.glob("*.exe")):
        data_dir = project_dir / f"{exe_path.stem}_Data"
        if not data_dir.is_dir():
            continue
        if any((data_dir / name).exists() for name in _DATA_MARKER_NAMES):
            return exe_path, data_dir
    return None


def _detect_backend(exe_path: Path, data_dir: Path) -> Backend | None:
    if (exe_path.parent / "GameAssembly.dll").is_file():
        return "il2cpp"
    if (data_dir / "Managed" / "Assembly-CSharp.dll").is_file():
        return "mono"
    return None


def _detect_arch(exe_path: Path) -> Arch:
    """读 PE 头 Machine 字段判定位数，不引入 pefile 依赖。DOS 头 e_lfanew（偏移
    0x3C 处 4 字节小端）指向 PE 头起始；PE 头是 4 字节签名 b"PE\\0\\0" 紧跟 2 字节
    Machine 字段（IMAGE_FILE_HEADER 第一个字段）。"""
    with exe_path.open("rb") as f:
        dos_header = f.read(64)
        if len(dos_header) < 64 or dos_header[:2] != b"MZ":
            raise InvalidPEFileError(f"{exe_path} 不是合法的 PE 文件（缺少 MZ 头）")
        (e_lfanew,) = struct.unpack_from("<I", dos_header, 0x3C)
        f.seek(e_lfanew)
        pe_header = f.read(6)
        if len(pe_header) < 6 or pe_header[:4] != b"PE\x00\x00":
            raise InvalidPEFileError(f"{exe_path} 不是合法的 PE 文件（缺少 PE 签名）")
        (machine,) = struct.unpack_from("<H", pe_header, 4)
    if machine == 0x014C:
        return "x86"
    if machine == 0x8664:
        return "x64"
    raise InvalidPEFileError(f"{exe_path} 是不支持的架构（machine=0x{machine:04x}），只支持 x86/x64")


def detect_unity(project_dir: Path) -> UnityTarget | None:
    found = _find_unity_data_dir(project_dir)
    if found is None:
        return None
    exe_path, data_dir = found
    backend = _detect_backend(exe_path, data_dir)
    if backend is None:
        return None
    try:
        arch = _detect_arch(exe_path)
    except InvalidPEFileError:
        return None
    return UnityTarget(exe_path=exe_path, data_dir=data_dir, backend=backend, arch=arch)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_detect.py -v`
Expected: PASS（7 个用例全绿）

- [ ] **Step 5: Commit**

```bash
git add src/rpg_translator/unity/__init__.py src/rpg_translator/unity/detect.py tests/test_unity_detect.py
git commit -m "feat: Unity 工程探测（Mono/IL2CPP + x86/x64 自动判定）"
```

---

## Task 2: Unity 占位符保护

**Files:**
- Create: `src/rpg_translator/unity/placeholders.py`
- Test: `tests/test_unity_placeholders.py`

**Interfaces:**
- Produces:
  - `protect(text: str) -> tuple[str, list[str]]`
  - `restore(text: str, tokens: list[str]) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_unity_placeholders.py
from __future__ import annotations

from rpg_translator.unity.placeholders import protect, restore


def test_protect_restore_roundtrip_is_identity_when_llm_leaves_tokens_untouched():
    original = "你好<color=#ff0000>世界</color>，欢迎 {player_name}，进度 %d%%\\n继续"
    protected, tokens = protect(original)

    assert "<color=#ff0000>" not in protected
    assert "{player_name}" not in protected
    assert restore(protected, tokens) == original


def test_protect_handles_tmp_rich_text_tags():
    protected, tokens = protect("<b>加粗</b>")
    assert len(tokens) == 2
    assert tokens[0] == "<b>"
    assert tokens[1] == "</b>"


def test_protect_handles_brace_placeholders():
    protected, tokens = protect("欢迎 {0}，你有 {count} 条消息")
    assert tokens == ["{0}", "{count}"]


def test_protect_handles_printf_style_format_specifiers():
    protected, tokens = protect("剩余 %d 次，%s 已完成 %1$s")
    assert tokens == ["%d", "%s", "%1$s"]


def test_protect_handles_escaped_newline():
    protected, tokens = protect("第一行\\n第二行")
    assert tokens == ["\\n"]


def test_protect_multiple_consecutive_placeholders_do_not_cross_contaminate():
    original = "{a}{b}{c}"
    protected, tokens = protect(original)
    assert tokens == ["{a}", "{b}", "{c}"]
    assert restore(protected, tokens) == original


def test_restore_leaves_unknown_token_index_untouched():
    """还原时如果译文里的 token 编号超出已记录范围（理论上不该发生，但要防
    LLM 输出异常导致 IndexError 崩掉整个请求），原样保留该 token 文本，不抛异常。"""
    protected, tokens = protect("{a}")
    mangled = protected.replace("0", "99", 1) if "0" in protected else protected
    # 只要不抛异常就算通过；具体保留成什么内容不强求。
    restore(mangled, tokens)


def test_protect_empty_string_returns_empty_with_no_tokens():
    protected, tokens = protect("")
    assert protected == ""
    assert tokens == []
    assert restore(protected, tokens) == ""


def test_protect_text_without_placeholders_is_unchanged():
    protected, tokens = protect("普通文本没有占位符")
    assert protected == "普通文本没有占位符"
    assert tokens == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_placeholders.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Implement `unity/placeholders.py`**

```python
# src/rpg_translator/unity/placeholders.py
from __future__ import annotations

import re

# 用私有区字符（U+E000/U+E001）包裹 token 编号——这两个码点属于 Unicode 私有
# 使用区，游戏原文/译文正常情况下不会出现，冲突概率可忽略。
_TOKEN_OPEN = ""
_TOKEN_CLOSE = ""
_TOKEN_RE = re.compile(f"{_TOKEN_OPEN}(\\d+){_TOKEN_CLOSE}")

# 第一版只做通用兜底，覆盖最常见的四类：TMP 富文本标签、花括号占位符、printf
# 风格格式化符、转义换行。未覆盖到的游戏特定标记允许译文里偶尔漏保护，后续按
# 实际反馈补规则。用 re.VERBOSE + 具名分组便于以后扩展新规则时看清楚每条规则
# 各自匹配什么，不是无差别囫囵一个大正则。
_COMBINED_RE = re.compile(
    r"""
    (?P<tag></?[a-zA-Z][^<>]*>)      # TMP 富文本标签：<color=#fff> </b> 等
  | (?P<brace>\{[^{}]*\})             # {0} / {player_name}
  | (?P<printf>%\d*\$?[sdif])         # %s / %d / %1$s
  | (?P<newline>\\n)                  # 转义换行
    """,
    re.VERBOSE,
)


def protect(text: str) -> tuple[str, list[str]]:
    """把文本里能识别的占位符依次替换成 <私有区>索引<私有区> token，返回替换后
    的文本和按出现顺序记录的原始片段列表，供 restore() 还原。"""
    tokens: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"{_TOKEN_OPEN}{len(tokens) - 1}{_TOKEN_CLOSE}"

    protected = _COMBINED_RE.sub(_replace, text)
    return protected, tokens


def restore(text: str, tokens: list[str]) -> str:
    """把 protect() 生成的 token 换回原始片段。译文里出现编号超出 tokens 范围
    的 token（正常不应发生，防御 LLM 输出异常）时原样保留该 token 文本，不
    抛异常——保底之下最多是这个占位符看着有点怪，不该让整条翻译请求失败。"""

    def _replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(tokens):
            return match.group(0)
        return tokens[index]

    return _TOKEN_RE.sub(_replace, text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_placeholders.py -v`
Expected: PASS（9 个用例全绿）

- [ ] **Step 5: Commit**

```bash
git add src/rpg_translator/unity/placeholders.py tests/test_unity_placeholders.py
git commit -m "feat: Unity 占位符保护（TMP 标签/花括号/printf/转义换行通用兜底）"
```

---

## Task 3: Unity 单条翻译 Prompt

**Files:**
- Create: `src/rpg_translator/unity/prompt.py`
- Test: `tests/test_unity_prompt.py`

**Interfaces:**
- Consumes: 无（纯字符串拼装，不依赖 Task 1/2）
- Produces:
  - `SYSTEM_PROMPT: str`
  - `build_user_prompt(protected_text: str, source_lang: str, target_lang: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_unity_prompt.py
from __future__ import annotations

from rpg_translator.unity.prompt import SYSTEM_PROMPT, build_user_prompt


def test_system_prompt_mentions_placeholder_preservation_rule():
    assert "" in SYSTEM_PROMPT or "占位符" in SYSTEM_PROMPT


def test_build_user_prompt_includes_text_and_languages():
    prompt = build_user_prompt("こんにちは0", "ja", "zh-CN")
    assert "こんにちは0" in prompt
    assert "ja" in prompt
    assert "zh-CN" in prompt


def test_build_user_prompt_does_not_mutate_protected_text():
    text = "保持不变1"
    prompt = build_user_prompt(text, "en", "zh-CN")
    assert text in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_prompt.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Implement `unity/prompt.py`**

```python
# src/rpg_translator/unity/prompt.py
from __future__ import annotations

# 不复用 translate/batch_translator.py 的 _TRANSLATE_SYSTEM_PROMPT——那份连
# "在线默认"策略都绑死了 RPG Maker 的 ⟦CCn⟧ 控制码占位符约定和"人名对照"批量
# 结构，不是通用日译中/英译中 prompt。这里单条、无历史、无术语表（shim 无
# 状态），只约束"保留占位符 token 原样、只输出译文"这类通用规则。
SYSTEM_PROMPT = (
    "你是专业的游戏本地化翻译。规则：\n"
    "1. 形如 N 的标记（N 是数字）是占位符，代表游戏原有的格式标签"
    "或变量，必须原样保留、不可翻译、不可移动、不可增删，两侧不加空格。\n"
    "2. 只输出译文本身，不要解释、引号或多余内容。\n"
    "3. 保留原文语气，不过度意译，不擅自增删换行。"
)


def build_user_prompt(protected_text: str, source_lang: str, target_lang: str) -> str:
    return f"将下面的文本从 {source_lang} 翻译成 {target_lang}：\n{protected_text}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_prompt.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/rpg_translator/unity/prompt.py tests/test_unity_prompt.py
git commit -m "feat: Unity 单条翻译 prompt（不复用 RPG Maker 批量 prompt）"
```

---

## Task 4: mod 部署 / 卸载

**Files:**
- Create: `src/rpg_translator/unity/deploy.py`
- Test: `tests/test_unity_deploy.py`

**Interfaces:**
- Consumes: `UnityTarget`（Task 1，字段 `exe_path`/`backend`/`arch`）
- Produces:
  - `DeployResult` (frozen dataclass): `manifest_path: Path`, `config_path: Path`, `deployed_files: list[str]`
  - `RemoveResult` (frozen dataclass): `removed: list[str]`, `restored: list[str]`
  - `deploy(target: UnityTarget, shim_port: int, resources_root: Path) -> DeployResult`
  - `remove(game_dir: Path) -> RemoveResult`
  - `UnsupportedVariantError(ValueError)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_unity_deploy.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rpg_translator.unity.deploy import (
    UnsupportedVariantError,
    deploy,
    remove,
)
from rpg_translator.unity.detect import UnityTarget


def _make_variant_dir(resources_root: Path, variant: str) -> Path:
    variant_dir = resources_root / "unity_mod" / variant
    (variant_dir / "BepInEx" / "core").mkdir(parents=True)
    (variant_dir / "winhttp.dll").write_bytes(b"fake-doorstop-proxy")
    (variant_dir / "doorstop_config.ini").write_text("[General]\nenabled = true\n", encoding="utf-8")
    (variant_dir / "BepInEx" / "core" / "BepInEx.dll").write_bytes(b"fake-bepinex-core")
    return variant_dir


def _make_target(tmp_path: Path, backend: str = "mono", arch: str = "x64") -> UnityTarget:
    game_dir = tmp_path / "Game"
    game_dir.mkdir()
    exe_path = game_dir / "Game.exe"
    exe_path.write_bytes(b"fake-exe")
    data_dir = game_dir / "Game_Data"
    data_dir.mkdir()
    return UnityTarget(exe_path=exe_path, data_dir=data_dir, backend=backend, arch=arch)


def test_deploy_copies_variant_files_into_game_dir(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path, backend="mono", arch="x64")

    result = deploy(target, shim_port=54321, resources_root=resources_root)

    game_dir = target.exe_path.parent
    assert (game_dir / "winhttp.dll").read_bytes() == b"fake-doorstop-proxy"
    assert (game_dir / "doorstop_config.ini").is_file()
    assert (game_dir / "BepInEx" / "core" / "BepInEx.dll").is_file()
    assert result.manifest_path.is_file()
    assert "winhttp.dll" in result.deployed_files


def test_deploy_writes_autotranslator_config_pointing_at_shim_port(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)

    result = deploy(target, shim_port=54321, resources_root=resources_root)

    content = result.config_path.read_text(encoding="utf-8")
    assert "Endpoint=CustomTranslate" in content
    assert "http://127.0.0.1:54321/translate" in content


def test_deploy_raises_for_unsupported_variant_combo(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    # 只准备 mono_x64，target 却要 il2cpp_x86，应该在找目录前就因为组合不在
    # 映射表里报错，不是笼统的 FileNotFoundError。
    target = _make_target(tmp_path, backend="il2cpp", arch="x86")

    with pytest.raises(UnsupportedVariantError):
        deploy(target, shim_port=1, resources_root=resources_root)
    # 已知合法组合但对应目录没准备好（还没跑 fetch 脚本）：FileNotFoundError。
    target2 = _make_target(tmp_path, backend="mono", arch="x86")
    with pytest.raises(FileNotFoundError):
        deploy(target2, shim_port=1, resources_root=resources_root)


def test_deploy_backs_up_preexisting_file_before_overwriting(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    (game_dir / "winhttp.dll").write_bytes(b"original-unrelated-winhttp")

    deploy(target, shim_port=1, resources_root=resources_root)

    backup = game_dir / ".rpg_translator_backup" / "unity_original" / "winhttp.dll"
    assert backup.read_bytes() == b"original-unrelated-winhttp"
    assert (game_dir / "winhttp.dll").read_bytes() == b"fake-doorstop-proxy"


def test_deploy_twice_does_not_overwrite_backup_with_our_own_first_deploy(tmp_path: Path):
    """第二次部署（比如换了 shim 端口重新部署）不能把第一次部署时已经写进去的
    我们自己的文件误当成"原文件"重新备份一遍，覆盖掉真正的原始备份。"""
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    (game_dir / "winhttp.dll").write_bytes(b"original-unrelated-winhttp")

    deploy(target, shim_port=1, resources_root=resources_root)
    deploy(target, shim_port=2, resources_root=resources_root)

    backup = game_dir / ".rpg_translator_backup" / "unity_original" / "winhttp.dll"
    assert backup.read_bytes() == b"original-unrelated-winhttp"


def test_remove_deletes_pure_additions_and_restores_backed_up_files(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    (game_dir / "winhttp.dll").write_bytes(b"original-unrelated-winhttp")

    deploy(target, shim_port=1, resources_root=resources_root)
    result = remove(game_dir)

    assert (game_dir / "winhttp.dll").read_bytes() == b"original-unrelated-winhttp"
    assert not (game_dir / "BepInEx" / "core" / "BepInEx.dll").exists()
    assert not (game_dir / "doorstop_config.ini").exists()
    assert "winhttp.dll" in result.restored
    assert "doorstop_config.ini" in result.removed
    assert not (game_dir / ".rpg_translator_unity" / "manifest.json").exists()


def test_remove_without_prior_deploy_is_a_noop(tmp_path: Path):
    game_dir = tmp_path / "Game"
    game_dir.mkdir()

    result = remove(game_dir)

    assert result.removed == []
    assert result.restored == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_deploy.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Implement `unity/deploy.py`**

```python
# src/rpg_translator/unity/deploy.py
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from rpg_translator.unity.detect import UnityTarget

_BACKUP_DIR_NAME = ".rpg_translator_backup"
_STATE_DIR_NAME = ".rpg_translator_unity"
_MANIFEST_NAME = "manifest.json"
_CONFIG_RELATIVE_PATH = Path("BepInEx") / "config" / "AutoTranslatorConfig.ini"

# Mono 用稳定版 BepInEx 5，IL2CPP 只能用 BepInEx 6 pre-release（v5 稳定版没有
# IL2CPP 支持）——见设计文档"已知限制"一节，不是这里能解决的问题。
_VARIANT_DIR = {
    ("mono", "x86"): "mono_x86",
    ("mono", "x64"): "mono_x64",
    ("il2cpp", "x86"): "il2cpp_x86",
    ("il2cpp", "x64"): "il2cpp_x64",
}


@dataclass(frozen=True)
class DeployResult:
    manifest_path: Path
    config_path: Path
    deployed_files: list[str]


@dataclass(frozen=True)
class RemoveResult:
    removed: list[str]
    restored: list[str]


class UnsupportedVariantError(ValueError):
    pass


def _variant_dir(target: UnityTarget, resources_root: Path) -> Path:
    key = (target.backend, target.arch)
    if key not in _VARIANT_DIR:
        raise UnsupportedVariantError(f"不支持的组合：{key}")
    return resources_root / "unity_mod" / _VARIANT_DIR[key]


def _game_dir(target: UnityTarget) -> Path:
    return target.exe_path.parent


def _backup_dir(game_dir: Path) -> Path:
    return game_dir / _BACKUP_DIR_NAME / "unity_original"


def _state_dir(game_dir: Path) -> Path:
    return game_dir / _STATE_DIR_NAME


def _manifest_path(game_dir: Path) -> Path:
    return _state_dir(game_dir) / _MANIFEST_NAME


def _to_posix(rel: Path) -> str:
    return str(rel).replace("\\", "/")


def _write_config(config_path: Path, shim_port: int) -> None:
    """BepInEx/config/AutoTranslatorConfig.ini 是社区广泛验证过的实际路径，
    但没有被 XUnity 第一方 README 逐字确认过——第一次真机部署要单独验证 XUnity
    启动时是读取这份已存在的 ini 还是会用默认值覆盖它（见设计文档"实测拉取后
    确认的几个点"）。"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "[Service]\n"
        "Endpoint=CustomTranslate\n"
        "\n"
        "[Custom]\n"
        f"Url=http://127.0.0.1:{shim_port}/translate\n",
        encoding="utf-8",
    )


def deploy(target: UnityTarget, shim_port: int, resources_root: Path) -> DeployResult:
    variant_dir = _variant_dir(target, resources_root)
    if not variant_dir.is_dir():
        raise FileNotFoundError(
            f"找不到 mod 素材目录 {variant_dir}，请先跑 scripts/fetch_unity_mod_assets.py"
        )
    game_dir = _game_dir(target)
    backup_dir = _backup_dir(game_dir)

    deployed: list[str] = []
    for src in sorted(variant_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(variant_dir)
        dest = game_dir / rel
        backup_dest = backup_dir / rel
        if dest.is_file() and not backup_dest.exists():
            backup_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup_dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        deployed.append(_to_posix(rel))

    config_path = game_dir / _CONFIG_RELATIVE_PATH
    config_backup = backup_dir / _CONFIG_RELATIVE_PATH
    if config_path.is_file() and not config_backup.exists():
        config_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, config_backup)
    _write_config(config_path, shim_port)
    config_rel = _to_posix(_CONFIG_RELATIVE_PATH)
    if config_rel not in deployed:
        deployed.append(config_rel)

    manifest_path = _manifest_path(game_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"deployed_files": deployed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return DeployResult(manifest_path=manifest_path, config_path=config_path, deployed_files=deployed)


def _cleanup_empty_dirs(path: Path) -> None:
    if not path.is_dir():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()
    if not any(path.iterdir()):
        path.rmdir()


def remove(game_dir: Path) -> RemoveResult:
    manifest_path = _manifest_path(game_dir)
    if not manifest_path.is_file():
        return RemoveResult(removed=[], restored=[])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backup_dir = _backup_dir(game_dir)

    removed: list[str] = []
    restored: list[str] = []
    for rel in manifest.get("deployed_files", []):
        dest = game_dir / rel
        backup_src = backup_dir / rel
        if backup_src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_src, dest)
            backup_src.unlink()
            restored.append(rel)
        elif dest.is_file():
            dest.unlink()
            removed.append(rel)

    manifest_path.unlink()
    _cleanup_empty_dirs(game_dir / "BepInEx")
    _cleanup_empty_dirs(backup_dir)
    _cleanup_empty_dirs(_state_dir(game_dir))
    return RemoveResult(removed=removed, restored=restored)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_deploy.py -v`
Expected: PASS（7 个用例全绿）

- [ ] **Step 5: Commit**

```bash
git add src/rpg_translator/unity/deploy.py tests/test_unity_deploy.py
git commit -m "feat: Unity mod 部署/卸载（覆盖前备份、manifest 精确回滚）"
```

---

## Task 5: 翻译 Shim 服务器

**Files:**
- Create: `src/rpg_translator/unity/translate_shim.py`
- Test: `tests/test_unity_translate_shim.py`

**Interfaces:**
- Consumes:
  - `protect(text: str) -> tuple[str, list[str]]` / `restore(text: str, tokens: list[str]) -> str`（Task 2）
  - `SYSTEM_PROMPT: str` / `build_user_prompt(protected_text, source_lang, target_lang) -> str`（Task 3）
  - `LLMClient(configs, ..., transports=...)` / `LLMConfig(api_key, base_url, model, timeout)` / `async LLMClient.chat(system_prompt, user_prompt) -> str`（已有，`translate/llm_client.py`）
- Produces:
  - `async def translate_text(config: LLMConfig, text: str, source_lang: str, target_lang: str, *, transport=None) -> str`
  - `class TranslateShimServer`: `__init__(self, config: LLMConfig, *, transport=None)`, `start() -> int`（返回端口）, `stop() -> None`, `is_running() -> bool`, `port` 属性

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_unity_translate_shim.py
from __future__ import annotations

import json

import httpx
import pytest

from rpg_translator.translate.llm_client import LLMConfig
from rpg_translator.unity.translate_shim import TranslateShimServer, translate_text


def _mock_transport(*, echo_prefix: str = "译:") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_msg = next(m["content"] for m in body["messages"] if m["role"] == "user")
        # 简单模拟：把 user prompt 里最后一行（协议保护后的原文）原样回显，
        # 前面加个前缀，用来验证占位符 token 真的原样传到了"LLM"侧又传了回来。
        payload_line = user_msg.splitlines()[-1]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": f"{echo_prefix}{payload_line}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    return httpx.MockTransport(handler)


def _config() -> LLMConfig:
    return LLMConfig(api_key="sk-test", base_url="http://mock.invalid/v1", model="test-model")


@pytest.mark.asyncio
async def test_translate_text_protects_and_restores_placeholders():
    text = "你好 {player_name}"
    result = await translate_text(_config(), text, "ja", "zh-CN", transport=_mock_transport())

    assert "{player_name}" in result
    assert result.startswith("译:")


def test_shim_server_start_returns_free_port_and_stop_releases_it():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port = server.start()

    assert server.is_running()
    assert isinstance(port, int) and port > 0

    server.stop()
    assert not server.is_running()


def test_shim_server_handles_translate_get_request_with_plain_text_response():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port = server.start()
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{port}/translate",
            params={"from": "ja", "to": "zh-CN", "text": "こんにちは"},
            timeout=5.0,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text.startswith("译:")
    finally:
        server.stop()


def test_shim_server_returns_400_when_text_param_missing():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port = server.start()
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/translate", params={"from": "ja", "to": "zh-CN"}, timeout=5.0)
        assert resp.status_code == 400
    finally:
        server.stop()


def test_shim_server_returns_404_for_unknown_path():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port = server.start()
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/unknown", timeout=5.0)
        assert resp.status_code == 404
    finally:
        server.stop()


def test_shim_server_start_twice_returns_same_port_without_restarting():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port1 = server.start()
    port2 = server.start()
    assert port1 == port2
    server.stop()


def test_shim_server_stop_without_start_is_a_noop():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    server.stop()  # 不应该抛异常
    assert not server.is_running()
```

这个模块用到 `pytest.mark.asyncio`——检查 `tests/conftest.py`/项目配置是否已经启用 `pytest-asyncio`（`test_llm_client.py` 已经在测异步的 `LLMClient.chat`，大概率已经配好，直接确认一下 `pyproject.toml`/`pytest.ini` 里的 `asyncio_mode` 设置，不用重新配置）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_translate_shim.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Implement `unity/translate_shim.py`**

```python
# src/rpg_translator/unity/translate_shim.py
from __future__ import annotations

import asyncio
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from rpg_translator.translate.llm_client import LLMClient, LLMConfig
from rpg_translator.unity.placeholders import protect, restore
from rpg_translator.unity.prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

_HOST = "127.0.0.1"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_HOST, 0))
        return sock.getsockname()[1]


async def translate_text(
    config: LLMConfig,
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """单条无状态翻译：保护占位符 -> 拼 prompt -> 调 LLMClient -> 还原占位符。
    每次请求起一个短生命周期的 LLMClient（而不是复用一个跨请求的单例）——
    XUnity 请求是交互式、低频的（用户读完一句文本才会触发下一句渲染），起一个
    新 httpx.AsyncClient 的开销可以忽略，换来的是不用操心跨线程/跨事件循环
    共享同一个 client 的生命周期问题（见下面 TranslateShimServer 里每个请求
    单独 asyncio.run() 的说明）。"""
    protected, tokens = protect(text)
    user_prompt = build_user_prompt(protected, source_lang, target_lang)
    async with LLMClient(config, transports=[transport] if transport is not None else None) as client:
        translated_protected = await client.chat(SYSTEM_PROMPT, user_prompt)
    return restore(translated_protected.strip(), tokens)


def _make_handler(
    config: LLMConfig, transport: httpx.BaseTransport | None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            logger.debug("shim: " + format, *args)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
            parsed = urlparse(self.path)
            if parsed.path != "/translate":
                self.send_response(404)
                self.end_headers()
                return

            params = parse_qs(parsed.query)
            text = params.get("text", [""])[0]
            if not text:
                self.send_response(400)
                self.end_headers()
                return
            source_lang = params.get("from", ["ja"])[0]
            target_lang = params.get("to", ["zh-CN"])[0]

            try:
                translated = asyncio.run(
                    translate_text(config, text, source_lang, target_lang, transport=transport)
                )
            except Exception:
                logger.exception("shim 翻译请求失败：%r", text)
                self.send_response(502)
                self.end_headers()
                return

            body = translated.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


class TranslateShimServer:
    """本机常驻 HTTP server，实现 XUnity.AutoTranslator 的 CustomTranslate 端点
    契约（GET /translate?from=&to=&text= -> 纯文本译文）。跟
    translate/local_engine.py 的 LocalEngineProcess 同一种"跟随 App 生命周期
    手动 start/stop"的用法，不是 QThread——GUI 层持有一个实例，deploy 前/中
    start()，closeEvent 里 stop()。"""

    def __init__(self, config: LLMConfig, *, transport: httpx.BaseTransport | None = None):
        self._config = config
        self._transport = transport
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None

    @property
    def port(self) -> int | None:
        return self._port

    def is_running(self) -> bool:
        return self._server is not None

    def start(self) -> int:
        if self._server is not None:
            assert self._port is not None
            return self._port

        port = _find_free_port()
        handler_cls = _make_handler(self._config, self._transport)
        server = ThreadingHTTPServer((_HOST, port), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        self._server = server
        self._thread = thread
        self._port = port
        return port

    def stop(self) -> None:
        if self._server is None:
            return
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._port = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_unity_translate_shim.py -v`
Expected: PASS（8 个用例全绿。如果 `pytest.mark.asyncio` 报 "unknown marker"，检查
`pyproject.toml`/`pytest.ini` 的 `[tool.pytest.ini_options] asyncio_mode = "auto"`
是否已配置——`test_llm_client.py` 现有的异步测试能跑通，说明已经配好，这里不需要
再改配置。）

- [ ] **Step 5: Commit**

```bash
git add src/rpg_translator/unity/translate_shim.py tests/test_unity_translate_shim.py
git commit -m "feat: Unity 翻译 shim 服务器（XUnity CustomTranslate 协议）"
```

---

## Task 6: BepInEx + XUnity.AutoTranslator 素材下载脚本

**Files:**
- Create: `scripts/fetch_unity_mod_assets.py`
- Test: `tests/test_fetch_unity_mod_assets.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `download(url, dest, *, expected_sha256=None, client=None, force=False, retries=..., sleep=...)` / `sha256_of(path)` / `ChecksumMismatchError`（已有，`scripts/build_full.py`，同目录下用 `importlib.util.spec_from_file_location` 动态加载，参考 `tests/test_build_full.py` 的 `_load_build_full()` 写法）
- Produces:
  - `extract_all(zip_path: Path, dest_dir: Path, *, skip_prefixes: tuple[str, ...] = ()) -> list[Path]`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 更新 `.gitignore`**

```gitignore
resources/unity_mod/
```

追加到文件末尾即可，不用调整已有条目的顺序。

- [ ] **Step 2: Write the failing test**

```python
# tests/test_fetch_unity_mod_assets.py
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path
from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fetch_unity_mod_assets.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_unity_mod_assets", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_unity_mod_assets = _load_module()


def _make_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def test_extract_all_preserves_directory_structure(tmp_path: Path):
    zip_path = tmp_path / "src.zip"
    _make_zip(
        zip_path,
        {
            "winhttp.dll": b"proxy",
            "BepInEx/core/BepInEx.dll": b"core",
        },
    )
    dest_dir = tmp_path / "out"

    extracted = fetch_unity_mod_assets.extract_all(zip_path, dest_dir)

    assert (dest_dir / "winhttp.dll").read_bytes() == b"proxy"
    assert (dest_dir / "BepInEx" / "core" / "BepInEx.dll").read_bytes() == b"core"
    assert len(extracted) == 2


def test_extract_all_overwrites_when_merging_second_zip(tmp_path: Path):
    dest_dir = tmp_path / "out"
    first = tmp_path / "first.zip"
    _make_zip(first, {"BepInEx/core/existing.dll": b"from-bepinex"})
    fetch_unity_mod_assets.extract_all(first, dest_dir)

    second = tmp_path / "second.zip"
    _make_zip(
        second,
        {
            "BepInEx/plugins/XUnity.AutoTranslator/plugin.dll": b"from-xunity",
        },
    )
    fetch_unity_mod_assets.extract_all(second, dest_dir)

    assert (dest_dir / "BepInEx" / "core" / "existing.dll").read_bytes() == b"from-bepinex"
    assert (dest_dir / "BepInEx" / "plugins" / "XUnity.AutoTranslator" / "plugin.dll").read_bytes() == b"from-xunity"


def test_extract_all_skips_paths_matching_skip_prefixes(tmp_path: Path):
    """XUnity 插件包里带一个跟翻译无关的独立插件 XUnity.ResourceRedirector/，
    合并时要能显式过滤掉，不能假设 XUnity 包解压出来只有 AutoTranslator 一个
    东西（见设计文档"实测拉取后确认的几个点"）。"""
    zip_path = tmp_path / "xunity.zip"
    _make_zip(
        zip_path,
        {
            "BepInEx/plugins/XUnity.AutoTranslator/plugin.dll": b"keep",
            "BepInEx/plugins/XUnity.ResourceRedirector/redirector.dll": b"skip",
        },
    )
    dest_dir = tmp_path / "out"

    extracted = fetch_unity_mod_assets.extract_all(
        zip_path, dest_dir, skip_prefixes=("BepInEx/plugins/XUnity.ResourceRedirector/",)
    )

    assert (dest_dir / "BepInEx" / "plugins" / "XUnity.AutoTranslator" / "plugin.dll").is_file()
    assert not (dest_dir / "BepInEx" / "plugins" / "XUnity.ResourceRedirector").exists()
    assert len(extracted) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fetch_unity_mod_assets.py -v`
Expected: FAIL（脚本文件还不存在）

- [ ] **Step 4: Implement `scripts/fetch_unity_mod_assets.py`**

```python
"""下载 Unity 运行时外挂翻译需要的 BepInEx + XUnity.AutoTranslator 四种变体
（Mono/IL2CPP × x86/x64），解压合并到 resources/unity_mod/。设计背景见
docs/superpowers/specs/2026-08-04-unity-runtime-translation-design.md。

用法：.venv\\Scripts\\python.exe scripts\\fetch_unity_mod_assets.py
产出：resources/unity_mod/{mono_x86,mono_x64,il2cpp_x86,il2cpp_x64}/

不进 git（体积大、第三方二进制），resources/unity_mod/ 已加进 .gitignore。跟
build_full.py 一样不在自动化测试/CI 里跑（要联网下载），自动化测试只覆盖内部
纯函数 extract_all。下载复用 build_full.py 已有的 sha256 校验 + 断点续传 +
重试逻辑，不重新造轮子。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import zipfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
UNITY_MOD_DIR = ROOT / "resources" / "unity_mod"


def _load_build_full() -> ModuleType:
    """跟 tests/test_build_full.py 用同一种方式动态加载——scripts/ 不是包，
    没有 __init__.py，不为了复用几个函数就把它改造成包结构。"""
    spec = importlib.util.spec_from_file_location(
        "build_full", Path(__file__).resolve().parent / "build_full.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_build_full = _load_build_full()
download = _build_full.download
ChecksumMismatchError = _build_full.ChecksumMismatchError

_BEPINEX_BASE_URL = os.environ.get(
    "BEPINEX_RELEASE_BASE_URL", "https://github.com/BepInEx/BepInEx/releases/download"
)
_XUNITY_BASE_URL = os.environ.get(
    "XUNITY_AUTOTRANSLATOR_RELEASE_BASE_URL",
    "https://github.com/bbepis/XUnity.AutoTranslator/releases/download",
)

_BEPINEX_MONO_TAG = "v5.4.23.5"
_BEPINEX_IL2CPP_TAG = "v6.0.0-pre.2"
_XUNITY_TAG = "v5.6.1"

# XUnity zip 里除了 XUnity.AutoTranslator/ 还带一个跟翻译无关的独立插件
# XUnity.ResourceRedirector/（贴图/字体资源重定向）。BepInEx 会自动加载
# plugins/ 下所有 dll，带着它等于多启用一个本设计不需要的插件，不符合
# "侵入性最低"，合并时显式跳过。
_XUNITY_SKIP_PREFIXES = ("BepInEx/plugins/XUnity.ResourceRedirector/",)

# sha256 是 2026-08-04 实测下载后核对过的值（见 resources/unity_mod/SOURCES.md），
# 后续如果改动上面的 tag/文件名，要重新核对并回填。
_ASSETS: dict[str, list[tuple[str, str | None, tuple[str, ...]]]] = {
    "mono_x64": [
        (
            f"{_BEPINEX_BASE_URL}/{_BEPINEX_MONO_TAG}/BepInEx_win_x64_5.4.23.5.zip",
            "82f9878551030f54657792c0740d9d51a09500eeae1fba21106b0c441e6732c4",
            (),
        ),
        (
            f"{_XUNITY_BASE_URL}/{_XUNITY_TAG}/XUnity.AutoTranslator-BepInEx-5.6.1.zip",
            "fbb7d1bbe2c7cc168da6dccbc500fb74786a85a548f52495c8a1592ac46407f5",
            _XUNITY_SKIP_PREFIXES,
        ),
    ],
    "mono_x86": [
        (
            f"{_BEPINEX_BASE_URL}/{_BEPINEX_MONO_TAG}/BepInEx_win_x86_5.4.23.5.zip",
            "37651c79e40d6f909572a4f461ac25350bb3ef8fe7fbd29f1aa8791a33b84c82",
            (),
        ),
        (
            f"{_XUNITY_BASE_URL}/{_XUNITY_TAG}/XUnity.AutoTranslator-BepInEx-5.6.1.zip",
            "fbb7d1bbe2c7cc168da6dccbc500fb74786a85a548f52495c8a1592ac46407f5",
            _XUNITY_SKIP_PREFIXES,
        ),
    ],
    "il2cpp_x64": [
        (
            f"{_BEPINEX_BASE_URL}/{_BEPINEX_IL2CPP_TAG}/BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip",
            "616ec7eb06cf11b2a0000e8fcef04d1b12bb58e84a2e0bdac9523234fc193ceb",
            (),
        ),
        (
            f"{_XUNITY_BASE_URL}/{_XUNITY_TAG}/XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip",
            "9d6b26e9d4957459bdb64b6d4852edb39cd5e8d31c28e0a157cefd6510ada811",
            _XUNITY_SKIP_PREFIXES,
        ),
    ],
    "il2cpp_x86": [
        (
            f"{_BEPINEX_BASE_URL}/{_BEPINEX_IL2CPP_TAG}/BepInEx-Unity.IL2CPP-win-x86-6.0.0-pre.2.zip",
            "cfef3a1e946dac5db8b9de4de1a922f47584dd775da32863f36762fbaad80f19",
            (),
        ),
        (
            f"{_XUNITY_BASE_URL}/{_XUNITY_TAG}/XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip",
            "9d6b26e9d4957459bdb64b6d4852edb39cd5e8d31c28e0a157cefd6510ada811",
            _XUNITY_SKIP_PREFIXES,
        ),
    ],
}


def extract_all(
    zip_path: Path, dest_dir: Path, *, skip_prefixes: tuple[str, ...] = ()
) -> list[Path]:
    """解压 zip 全部内容到 dest_dir，保留内部目录结构（跟 build_full.py 的
    extract_members 不同——那个按后缀摊平，这里 BepInEx/XUnity 的目录层级本身
    有意义，必须原样保留）。同名文件后解压的覆盖先解压的，用来把 XUnity 插件
    合并进已经解压好的 BepInEx 目录。skip_prefixes 匹配的成员直接跳过，不落盘。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if any(info.filename.startswith(prefix) for prefix in skip_prefixes):
                continue
            target = dest_dir / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            extracted.append(target)
    return extracted


def _fetch_variant(
    variant: str,
    assets: list[tuple[str, str | None, tuple[str, ...]]],
    work_dir: Path,
    force: bool,
) -> None:
    dest_dir = UNITY_MOD_DIR / variant
    for url, expected_sha256, skip_prefixes in assets:
        zip_name = url.rsplit("/", 1)[-1]
        zip_path = work_dir / zip_name
        download(url, zip_path, expected_sha256=expected_sha256, force=force)
        extract_all(zip_path, dest_dir, skip_prefixes=skip_prefixes)
    print(f"[fetch_unity_mod_assets] {variant} 就绪：{dest_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=UNITY_MOD_DIR / "_downloads")
    parser.add_argument("--force-redownload", action="store_true")
    args = parser.parse_args(argv)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    for variant, assets in _ASSETS.items():
        _fetch_variant(variant, assets, args.work_dir, args.force_redownload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fetch_unity_mod_assets.py -v`
Expected: PASS（3 个用例全绿，不联网）

- [ ] **Step 6: 用已经在本地的真实素材验证脚本产出跟手动摸出来的结构一致（不是自动化测试，是一次性人工核对）**

`resources/unity_mod/` 目录下已经有一份手动下载核对过的素材（`resources/unity_mod/SOURCES.md` 记录了来源）。跑一遍：

```bash
.venv\Scripts\python.exe scripts\fetch_unity_mod_assets.py --work-dir resources\unity_mod\_downloads
```

比对脚本重新产出的 `resources/unity_mod/{variant}/` 内容和当前已有内容一致（sha256 都对得上说明本地缓存命中，`extract_all` 的解压结果应该逐文件相同）；确认 `BepInEx/plugins/XUnity.ResourceRedirector/` 目录在脚本产出里确实不存在。

- [ ] **Step 7: Commit**

```bash
git add scripts/fetch_unity_mod_assets.py tests/test_fetch_unity_mod_assets.py .gitignore
git commit -m "feat: BepInEx+XUnity.AutoTranslator 素材下载脚本"
```

（`resources/unity_mod/` 本身已被 `.gitignore` 排除，不会被这次提交带进去。）

---

## Task 7: GUI 集成

**Files:**
- Modify: `src/rpg_translator/gui/main_window.py`
- Test: `tests/test_gui.py`（追加用例）

**Interfaces:**
- Consumes:
  - `detect_unity(project_dir: Path) -> UnityTarget | None`（Task 1）
  - `deploy(target, shim_port, resources_root) -> DeployResult` / `remove(game_dir) -> RemoveResult`（Task 4）
  - `TranslateShimServer(config, *, transport=None)`（Task 5）
  - `ENGINE_LOCAL`, `resolve_local_config`, `resolve_base_url`, `resolve_fallback_config`（已有，`gui/settings_dialog.py`）
  - `get_deepseek_api_key`（已有，`config.py`）
  - `LLMConfig`（已有，`translate/llm_client.py`）
  - `get_app_root()`（已有，`translate/local_engine.py`，"frozen 用 exe 目录、开发环境用项目根目录"的 `resources/` 定位方式）

- [ ] **Step 1: Write the failing tests（追加到 `tests/test_gui.py`）**

```python
# 追加到 tests/test_gui.py 末尾

from rpg_translator.unity.detect import UnityTarget
from rpg_translator.unity.deploy import DeployResult, RemoveResult


def _make_unity_project(tmp_path: Path) -> Path:
    """跟 tests/test_unity_detect.py 里的假 Mono 工程构造方式一致，供 GUI 层
    拖拽识别测试用。"""
    import struct

    game_dir = tmp_path / "UnityGame"
    game_dir.mkdir()
    exe = game_dir / "Game.exe"
    dos_header = bytearray(64)
    dos_header[0:2] = b"MZ"
    struct.pack_into("<I", dos_header, 0x3C, 64)
    pe_header = b"PE\x00\x00" + struct.pack("<H", 0x8664) + b"\x00" * 18
    exe.write_bytes(bytes(dos_header) + pe_header)
    data_dir = game_dir / "Game_Data"
    (data_dir / "Managed").mkdir(parents=True)
    (data_dir / "globalgamemanagers").write_bytes(b"\x00")
    (data_dir / "Managed" / "Assembly-CSharp.dll").write_bytes(b"\x00")
    return game_dir


def test_drop_unity_project_shows_unity_panel_instead_of_rpgmaker_panel(qapp, tmp_path: Path):
    window = MainWindow()
    unity_project = _make_unity_project(tmp_path)

    window._on_path_dropped(unity_project)

    assert window._unity_target is not None
    assert window._unity_target.backend == "mono"
    assert window._unity_deploy_box.isVisible()
    assert not window._translate_box.isVisible()


def test_unity_deploy_button_calls_deploy_and_starts_shim_server(qapp, tmp_path: Path, monkeypatch):
    window = MainWindow()
    unity_project = _make_unity_project(tmp_path)
    window._on_path_dropped(unity_project)

    fake_result = DeployResult(
        manifest_path=tmp_path / "manifest.json", config_path=tmp_path / "config.ini", deployed_files=["a"]
    )
    called = {}

    def fake_deploy(target, shim_port, resources_root):
        called["target"] = target
        called["shim_port"] = shim_port
        return fake_result

    monkeypatch.setattr("rpg_translator.gui.main_window.deploy", fake_deploy)
    monkeypatch.setattr(
        "rpg_translator.gui.main_window.TranslateShimServer.start", lambda self: 54321
    )

    window._on_unity_deploy_clicked()

    assert called["target"] is window._unity_target
    assert called["shim_port"] == 54321


def test_unity_remove_button_calls_remove(qapp, tmp_path: Path, monkeypatch):
    window = MainWindow()
    unity_project = _make_unity_project(tmp_path)
    window._on_path_dropped(unity_project)

    called = {}
    monkeypatch.setattr(
        "rpg_translator.gui.main_window.remove",
        lambda game_dir: called.setdefault("game_dir", game_dir) or RemoveResult(removed=["a"], restored=[]),
    )

    window._on_unity_remove_clicked()

    assert called["game_dir"] == unity_project


def test_unity_panel_shows_sakura_warning_when_local_engine_selected(qapp, tmp_path: Path):
    from PySide6.QtCore import QSettings

    from rpg_translator.gui.settings_dialog import APP_NAME, ENGINE_LOCAL, ORG_NAME

    qsettings = QSettings(ORG_NAME, APP_NAME)
    qsettings.setValue("engine", ENGINE_LOCAL)

    window = MainWindow()
    unity_project = _make_unity_project(tmp_path)
    window._on_path_dropped(unity_project)

    assert "Sakura" in window._unity_sakura_warning_label.text()
    assert window._unity_sakura_warning_label.isVisible()

    qsettings.remove("engine")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui.py -k unity -v`
Expected: FAIL（`AttributeError: 'MainWindow' object has no attribute '_unity_target'` 等）

- [ ] **Step 3: 修改 `gui/main_window.py`**

在文件顶部 import 区追加：

```python
from rpg_translator.translate.llm_client import LLMConfig
from rpg_translator.translate.local_engine import get_app_root
from rpg_translator.unity.deploy import DeployResult, RemoveResult, deploy, remove
from rpg_translator.unity.detect import UnityTarget, detect_unity
from rpg_translator.unity.translate_shim import TranslateShimServer
```

在 `MainWindow.__init__` 里，`self._local_engine_process` 定义附近追加实例属性：

```python
        self._unity_target: UnityTarget | None = None
        self._unity_shim_server: TranslateShimServer | None = None
```

在 `inject_box`（阶段 3 的 QGroupBox，`main_window.py:460` 附近）构造完之后、加入主布局之前，新增一个平行的 Unity 面板（`translate_box`/`inject_box` 命名对应现有阶段 2/3；这里补一组同级但只在识别到 Unity 工程时可见的控件）：

```python
        self._unity_deploy_button = QPushButton("部署翻译外挂")
        self._unity_deploy_button.clicked.connect(self._on_unity_deploy_clicked)

        self._unity_remove_button = QPushButton("卸载还原")
        self._unity_remove_button.setObjectName("secondaryButton")
        self._unity_remove_button.clicked.connect(self._on_unity_remove_clicked)

        self._unity_sakura_warning_label = QLabel(
            "当前翻译引擎是本地 Sakura（RPG Maker 语法特化模型），翻译 Unity 游戏"
            "建议在设置里切换在线 Provider。"
        )
        self._unity_sakura_warning_label.setObjectName("infoLabel")
        self._unity_sakura_warning_label.setWordWrap(True)
        self._unity_sakura_warning_label.setVisible(False)

        self._unity_status_label = QLabel("")
        self._unity_status_label.setObjectName("infoLabel")
        self._unity_status_label.setWordWrap(True)

        # 注意：不能直接把现有 self._open_output_button 塞进这个新 QHBoxLayout——
        # 一个 QWidget 同时只能属于一个 layout，重复 addWidget 会把它从原来的
        # inject_row 里挪走。这里另建一个同样打开 self._project_dir 的按钮实例。
        self._unity_open_output_button = QPushButton("打开游戏文件夹")
        self._unity_open_output_button.setObjectName("secondaryButton")
        self._unity_open_output_button.clicked.connect(self._on_open_output_clicked)

        unity_row = QHBoxLayout()
        unity_row.addWidget(self._unity_deploy_button)
        unity_row.addWidget(self._unity_remove_button)
        unity_row.addWidget(self._unity_open_output_button)
        unity_row.addStretch(1)

        self._unity_deploy_box = QGroupBox(
            "2. 部署（运行时外挂翻译：装好后自行启动游戏即可，翻译发生在游戏运行"
            "期间，不产出译文文件，不改游戏文件本体，随时可卸载还原）"
        )
        unity_deploy_layout = QVBoxLayout(self._unity_deploy_box)
        unity_deploy_layout.addWidget(self._unity_sakura_warning_label)
        unity_deploy_layout.addLayout(unity_row)
        unity_deploy_layout.addWidget(self._unity_status_label)
        self._unity_deploy_box.setVisible(False)
```

主布局里紧跟着 `translate_box`/`inject_box` 加进去的位置，把 `self._unity_deploy_box` 也 `addWidget` 进去（跟 `translate_box`/`inject_box` 同一个 `QVBoxLayout`，顺序在它们之后即可，可见性靠 `setVisible` 互斥，不需要 `QStackedWidget`）。

在 `_on_path_dropped`（`main_window.py:582`）的 `except UnknownEngineError:` 分支里，`find_evb_candidate` 检查之后、"未识别到支持的 RPG Maker 引擎"提示之前，插入 Unity 探测：

```python
        except UnknownEngineError:
            evb_candidate = find_evb_candidate(path)
            if evb_candidate is not None:
                self._start_evb_unpack(evb_candidate)
                return False

            unity_target = detect_unity(path)
            if unity_target is not None:
                self._show_unity_panel(path, unity_target)
                return True

            self._info_label.setText("未识别到支持的 RPG Maker 引擎")
            self._adapter = None
            self._start_button.setEnabled(False)
            self._switch_original_button.setEnabled(False)
            self._switch_translated_button.setEnabled(False)
            self._open_output_button.setVisible(False)
            return False
```

紧跟 `self._project_dir = path`（`main_window.py:602`，在 `try: adapter = detect_adapter(path)` 之前）补一段无条件重置，覆盖"先拖了个 Unity 工程、又拖了个 RPG Maker 工程（或反过来）"时两个面板不会同时显示、也不会残留上一次识别结果的情况：

```python
        self._project_dir = path
        self._unity_target = None
        self._unity_deploy_box.setVisible(False)
        self._translate_box.setVisible(True)
        self._inject_box.setVisible(True)
        try:
            adapter = detect_adapter(path)
        except UnknownEngineError:
            ...
```

（`self._translate_box`/`self._inject_box` 目前是局部变量，不是实例属性——顺手把它们也提升成 `self._translate_box`/`self._inject_box`，在 `__init__` 里赋值处加 `self.` 前缀，这样才能在这里和 `_show_unity_panel` 里控制它们的可见性。`_show_unity_panel` 内部会再把这两个设回 `False`，这里先无条件设 `True` 是为了"识别到 RPG Maker 引擎"这条路径不用再单独写一遍。）

新增方法（放在 `_on_preview_extract_failed` 附近）：

```python
    def _show_unity_panel(self, project_dir: Path, target: UnityTarget) -> None:
        self._unity_target = target
        self._translate_box.setVisible(False)
        self._inject_box.setVisible(False)
        self._unity_deploy_box.setVisible(True)
        backend_label = {"mono": "Mono", "il2cpp": "IL2CPP"}[target.backend]
        self._info_label.setText(
            f"识别到 Unity 工程（{backend_label} / {target.arch}）：{target.exe_path.name}"
        )
        self._unity_status_label.setText("尚未部署")

        qsettings = QSettings(ORG_NAME, APP_NAME)
        is_local = qsettings.value("engine", "online") == ENGINE_LOCAL
        self._unity_sakura_warning_label.setVisible(is_local)

    def _resolve_shim_llm_config(self) -> LLMConfig:
        """跟 _start_translate_worker（main_window.py:811 附近）的引擎分流逻辑
        保持一致：本地 Sakura 也能用（只警告不拦，见设计文档"本地引擎限制"），
        在线走 DeepSeek 等 provider。Unity 场景不需要 batch_size/并发/fallback
        这些批量翻译才有意义的参数，shim 是单条无状态请求。"""
        qsettings = QSettings(ORG_NAME, APP_NAME)
        if qsettings.value("engine", "online") == ENGINE_LOCAL:
            api_key, base_url, model = resolve_local_config(qsettings)
            if (not base_url or not model) and self._bundled_local_base_url:
                base_url = self._bundled_local_base_url
                model = LOCAL_ENGINE_MODEL_ALIAS
            return LLMConfig(api_key=api_key, base_url=base_url, model=model, timeout=180.0)
        base_url = resolve_base_url(qsettings)
        model = str(qsettings.value("model", "deepseek-v4-flash"))
        api_key = get_deepseek_api_key()
        return LLMConfig(api_key=api_key, base_url=base_url, model=model, timeout=60.0)

    def _on_unity_deploy_clicked(self) -> None:
        if self._unity_target is None:
            return
        try:
            config = self._resolve_shim_llm_config()
        except MissingApiKeyError as e:
            QMessageBox.warning(self, "缺少 API Key", str(e))
            return

        if self._unity_shim_server is None:
            self._unity_shim_server = TranslateShimServer(config)
        port = self._unity_shim_server.start()

        try:
            result = deploy(self._unity_target, port, get_app_root())
        except Exception as e:
            logger.exception("Unity mod 部署失败")
            QMessageBox.critical(self, "部署失败", str(e))
            return

        self._unity_status_label.setText(
            f"部署完成，共写入/覆盖 {len(result.deployed_files)} 个文件。"
            "请自行启动游戏（Steam/直接运行 exe 均可）。"
        )

    def _on_unity_remove_clicked(self) -> None:
        if self._unity_target is None:
            return
        game_dir = self._unity_target.exe_path.parent
        try:
            result = remove(game_dir)
        except Exception as e:
            logger.exception("Unity mod 卸载失败")
            QMessageBox.critical(self, "卸载失败", str(e))
            return
        self._unity_status_label.setText(
            f"卸载完成：还原 {len(result.restored)} 个文件，删除 {len(result.removed)} 个新增文件。"
        )
        self._stop_unity_shim_server()

    def _stop_unity_shim_server(self) -> None:
        if self._unity_shim_server is not None:
            self._unity_shim_server.stop()
```

`_resolve_shim_llm_config` 用到的 `MissingApiKeyError` 从 `rpg_translator.core.pipeline` import（已有异常类，`get_deepseek_api_key()` 返回 `None` 时现有 `_require_api_key` 会抛这个，这里手动检查一下 `api_key` 是否为空、为空时抛同一个异常类型，保持跟现有 RPG Maker 路径一致的报错方式）：

```python
from rpg_translator.core.pipeline import MissingApiKeyError, UnknownEngineError, ...
```

`_resolve_shim_llm_config` 里 `get_deepseek_api_key()` 之后补一行：

```python
        api_key = get_deepseek_api_key()
        if not api_key:
            raise MissingApiKeyError("未配置 DeepSeek API Key，请先在设置里配置。")
```

`closeEvent`（`main_window.py:981`）里 `self._stop_local_engine()` 调用处一并调用：

```python
        self._stop_local_engine()
        self._stop_unity_shim_server()
        event.accept()
```

（两处 `self._stop_local_engine()` 调用——正常关闭路径和有 worker 在跑等超时后的路径——都要加对应的 `self._stop_unity_shim_server()`。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui.py -v`
Expected: PASS（全部用例，包括新增的 5 个 Unity 相关用例和之前所有 RPG Maker 相关用例——改动共享了 `_on_path_dropped`，必须确认没有破坏现有分支）

- [ ] **Step 5: Commit**

```bash
git add src/rpg_translator/gui/main_window.py tests/test_gui.py
git commit -m "feat: GUI 集成 Unity 运行时外挂翻译（拖拽自动识别、部署/卸载面板）"
```

---

## Task 8: 全量回归 + 真机验证清单

**Files:** 无新增/修改文件，纯验证。

- [ ] **Step 1: 跑全量测试套件**

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: 全部 PASS，包括 Task 1-7 新增的所有用例和项目原有测试（确认 Unity 分支的改动没有影响 RPG Maker 路径）。

- [ ] **Step 2: 语法/类型健全性检查**

Run:
```bash
.venv\Scripts\python.exe -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) or print(f, 'OK') for f in ['src/rpg_translator/unity/detect.py', 'src/rpg_translator/unity/placeholders.py', 'src/rpg_translator/unity/prompt.py', 'src/rpg_translator/unity/deploy.py', 'src/rpg_translator/unity/translate_shim.py', 'scripts/fetch_unity_mod_assets.py', 'src/rpg_translator/gui/main_window.py']]"
```
Expected: 每个文件都打印 `OK`。

- [ ] **Step 3: 真机验证清单（人工操作，不是自动化步骤）**

如果手头有真实 Unity 游戏样本（Mono 优先，成熟度比 IL2CPP 高，见设计文档"已知限制"），按顺序验证：

1. 拖游戏目录进 App，确认识别出"Unity 工程（Mono/IL2CPP / x86/x64）"且面板正确切换。
2. 点「部署翻译外挂」，确认 `游戏目录/winhttp.dll`、`游戏目录/BepInEx/` 落地，`游戏目录/BepInEx/config/AutoTranslatorConfig.ini` 内容指向正确端口。
3. 手动启动游戏 exe，确认游戏正常起来（doorstop 注入没有导致游戏崩溃/无法启动——这是最基本的"没有破坏游戏"验证）。
4. 游戏内触发文本渲染，确认：
   a. XUnity 面板能用 `ALT+0` 呼出，说明 mod 确实生效；
   b. 文本被替换成中文，说明请求确实打到了本地 shim、shim 确实调用了 LLM 并返回了译文；
   c. **重点确认第 2 步写入的 `AutoTranslatorConfig.ini` 是否被 XUnity 首次启动时保留/读取，而不是被覆盖成默认值**（design 文档标注的唯一没有把握、必须实测的点——如果被覆盖，需要换个思路，比如改成"游戏启动后再写入"或者研究 XUnity 是否支持通过命令行/环境变量指定 Endpoint）。
5. 关闭游戏，点「卸载还原」，确认 `游戏目录` 恢复到部署前的状态（`winhttp.dll`/`BepInEx/` 等新增文件消失，游戏能在没有 mod 的情况下正常启动）。

没有真实样本的话，这一步只能靠 Task 1-7 的单测覆盖"文件/协议层正确"，实际游戏内表现无法自动化验证——这是设计文档里已经写明的已知局限，如实告知用户，不假装验证过。

- [ ] **Step 4: 无需 commit**（这个任务不产生代码改动，是验证性任务）

---

## Self-Review Notes

- **Spec 覆盖**：设计文档 1-6 节分别对应 Task 1（探测）、Task 4（部署/卸载）、Task 5（shim/占位符/prompt，拆成 Task 2/3/5 三个任务落地）、Task 7（GUI）、Task 6（素材下载）；"不包含"里列的静态提取/术语表持久化/自定义游戏内 UI/非 Windows 平台在计划里都没有对应任务，符合设计文档"不包含"范围。
- **类型一致性**：`UnityTarget`（Task 1 产出）在 Task 4/7 里字段名（`exe_path`/`data_dir`/`backend`/`arch`）保持一致；`DeployResult`/`RemoveResult`（Task 4 产出）在 Task 7 测试里的字段名（`manifest_path`/`config_path`/`deployed_files`/`removed`/`restored`）保持一致；`TranslateShimServer.start() -> int`（Task 5 产出）在 Task 7 里作为端口号直接传给 `deploy()` 的 `shim_port` 参数，类型对得上。
- **已知未决项**（如实标注，不是遗漏）：`BepInEx/config/AutoTranslatorConfig.ini` 的具体路径是社区共识但未经第一方文档逐字确认，Task 8 Step 3 第 4.c 点是唯一的验证手段；没有真实 Unity 游戏样本时这一验证无法自动化完成。
