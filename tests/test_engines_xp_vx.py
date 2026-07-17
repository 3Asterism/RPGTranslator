from __future__ import annotations

from pathlib import Path

from rpg_translator.codec.rvdata2_codec import read_rvdata2
from rpg_translator.core.ir import TextUnit
from rpg_translator.engines.xp_vx import VXAdapter, XPAdapter


def _by_locator(units: list[TextUnit], file_path: str, locator: str) -> TextUnit:
    for u in units:
        if u.file_path == file_path and u.locator == locator:
            return u
    raise AssertionError(f"no unit at {file_path} {locator}")


def test_xp_detected_via_actors_rxdata(xp_project: Path):
    assert XPAdapter.detect(xp_project) is True


def test_xp_not_detected_on_unrelated_dir(tmp_path: Path):
    assert XPAdapter.detect(tmp_path) is False


def test_xp_extract_decodes_bytes_valued_strings_not_python_repr(xp_project: Path):
    """M4.9 用真实 XP 工程（torresflo/Pokemon-Obsidian）实测发现的 bug：老版本
    Ruby（1.8）marshal 出来的字符串在 rubymarshal 里原样是 `bytes`，之前的代码
    对它调用 Python `str()` 会得到 `"b'...'"` 这种 repr 字面量而不是真正的文本。
    这条 fixture 特意用 `bytes` 值复现真实格式，保证这个 bug 有回归测试兜底。"""
    units = XPAdapter().extract(xp_project)

    message = _by_locator(units, "Data/Map001.rxdata", "@events/1/@pages/0/@list/0/@parameters/0")
    assert message.source_text == "Bonjour, voyageur.\nBienvenue au village."
    assert "b'" not in message.source_text

    actor_name = _by_locator(units, "Data/Actors.rxdata", "1/@name")
    assert actor_name.source_text == "Rouge"

    choice = _by_locator(units, "Data/Map001.rxdata", "@events/1/@pages/0/@list/2/@parameters/0/0")
    assert choice.source_text == "Oui"


def test_xp_extract_finds_change_name_and_common_event(xp_project: Path):
    units = XPAdapter().extract(xp_project)

    change_name = _by_locator(units, "Data/Map001.rxdata", "@events/1/@pages/0/@list/4/@parameters/1")
    assert change_name.source_text == "Heros"

    common = _by_locator(units, "Data/CommonEvents.rxdata", "1/@list/0/@parameters/0")
    assert common.source_text == "Texte d'evenement commun."


def test_xp_extract_skips_comment_command(xp_project: Path):
    units = XPAdapter().extract(xp_project)
    locators = {u.locator for u in units if u.file_path == "Data/Map001.rxdata"}
    assert "@events/1/@pages/0/@list/3/@parameters/0" not in locators  # code 108 Comment


def _all_rxdata_files(root: Path) -> list[Path]:
    return sorted(p.relative_to(root) for p in root.rglob("*.rxdata"))


def test_xp_roundtrip_untranslated_inject_is_byte_identical(tmp_path: Path, xp_project: Path):
    """未翻译回填必须逐字节还原——包括 bytes 类型的字段写回时要编码回
    bytes（不能变成 rubymarshal 默认给 str 加的 ivar/UTF-8 标记包装），
    格式跟原始的老版本 Ruby 输出保持一致。"""
    adapter = XPAdapter()
    units = adapter.extract(xp_project)

    output_dir = tmp_path / "output"
    adapter.inject(xp_project, units, output_dir)

    original_files = _all_rxdata_files(xp_project)
    output_files = _all_rxdata_files(output_dir)
    assert original_files == output_files
    assert original_files

    for rel in original_files:
        assert (output_dir / rel).read_bytes() == (xp_project / rel).read_bytes(), (
            f"{rel} differs after untranslated round trip"
        )


def test_xp_inject_translated_text_encodes_back_to_bytes(tmp_path: Path, xp_project: Path):
    adapter = XPAdapter()
    units = adapter.extract(xp_project)

    target = _by_locator(units, "Data/Actors.rxdata", "1/@name")
    target.translated_text = "红"

    output_dir = tmp_path / "output"
    adapter.inject(xp_project, units, output_dir)

    actors = read_rvdata2(output_dir / "Data" / "Actors.rxdata")
    raw_value = actors[1].attributes["@name"]
    assert isinstance(raw_value, bytes), "回填后应保持原有的 bytes 类型，不能悄悄变成 str"
    assert raw_value.decode("utf-8") == "红"

    # 重新用 adapter 抽取一遍，确认能正常读回译文（不会因为编码问题读出乱码）
    reloaded = XPAdapter().extract(output_dir)
    reloaded_unit = _by_locator(reloaded, "Data/Actors.rxdata", "1/@name")
    assert reloaded_unit.source_text == "红"


def test_xp_inject_does_not_mutate_source_project(tmp_path: Path, xp_project: Path):
    adapter = XPAdapter()
    units = adapter.extract(xp_project)
    before = (xp_project / "Data" / "Map001.rxdata").read_bytes()

    units[0].translated_text = "whatever"
    adapter.inject(xp_project, units, tmp_path / "output")

    after = (xp_project / "Data" / "Map001.rxdata").read_bytes()
    assert before == after


# ---------------------------------------------------------------------------
# VX（非 Ace）——和 XP 共用 RGSSAdapterBase，用真实工程
# ambratolm-games/flower-in-pain（M4.9）验证过整套 extract/inject。这里只
# 补最基本的 fixture 覆盖，bytes 编码这条真机 bug 已经在上面 XP 的用例里
# 覆盖过一遍（共用同一份 `_rgss_common.py` 代码）。
# ---------------------------------------------------------------------------


def test_vx_detected_via_actors_rvdata(vx_project: Path):
    assert VXAdapter.detect(vx_project) is True


def test_vx_extract_decodes_bytes_valued_strings(vx_project: Path):
    units = VXAdapter().extract(vx_project)
    message = _by_locator(units, "Data/Map001.rvdata", "@events/1/@pages/0/@list/0/@parameters/0")
    assert message.source_text == "Bonjour."
    actor_name = _by_locator(units, "Data/Actors.rvdata", "1/@name")
    assert actor_name.source_text == "Rouge"


def test_vx_roundtrip_untranslated_inject_is_byte_identical(tmp_path: Path, vx_project: Path):
    adapter = VXAdapter()
    units = adapter.extract(vx_project)

    output_dir = tmp_path / "output"
    adapter.inject(vx_project, units, output_dir)

    for rel in sorted(p.relative_to(vx_project) for p in vx_project.rglob("*.rvdata")):
        assert (output_dir / rel).read_bytes() == (vx_project / rel).read_bytes(), (
            f"{rel} differs after untranslated round trip"
        )


def test_vx_inject_translated_text_round_trips(tmp_path: Path, vx_project: Path):
    adapter = VXAdapter()
    units = adapter.extract(vx_project)
    target = _by_locator(units, "Data/Actors.rvdata", "1/@name")
    target.translated_text = "红"

    output_dir = tmp_path / "output"
    adapter.inject(vx_project, units, output_dir)

    reloaded = VXAdapter().extract(output_dir)
    reloaded_unit = _by_locator(reloaded, "Data/Actors.rvdata", "1/@name")
    assert reloaded_unit.source_text == "红"
