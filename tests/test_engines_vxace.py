from __future__ import annotations

from pathlib import Path

from rpg_translator.core.ir import TextUnit
from rpg_translator.engines.vxace import VXAceAdapter


def _by_locator(units: list[TextUnit], file_path: str, locator: str) -> TextUnit:
    for u in units:
        if u.file_path == file_path and u.locator == locator:
            return u
    raise AssertionError(f"no unit at {file_path} {locator}")


def test_vxace_detected_via_actors_rvdata2(vxace_project: Path):
    assert VXAceAdapter.detect(vxace_project) is True


def test_vxace_not_detected_on_unrelated_dir(tmp_path: Path):
    assert VXAceAdapter.detect(tmp_path) is False


def test_vxace_extract_merges_consecutive_show_text_lines_into_one_unit(vxace_project: Path):
    """VX Ace 消息框不自动换行且固定 4 行，同一条 Show Text 的连续行要合并成一个
    段落一起翻译（spec 第 9 节），不能逐行翻译——否则译文长度和原文行数对不上会溢出。"""
    units = VXAceAdapter().extract(vxace_project)
    file_path = "Data/Map001.rvdata2"
    prefix = "@events/1/@pages/0/@list"

    message = _by_locator(units, file_path, f"{prefix}/0/@parameters/0")
    assert message.source_text == "こんにちは、旅人よ。\nこの村へようこそ。"
    assert message.extra_locators == [f"{prefix}/1/@parameters/0"]

    choice0 = _by_locator(units, file_path, f"{prefix}/2/@parameters/0/0")
    choice1 = _by_locator(units, file_path, f"{prefix}/2/@parameters/0/1")
    assert choice0.source_text == "はい"
    assert choice1.source_text == "いいえ"


def test_vxace_extract_finds_change_name_command(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    unit = _by_locator(
        units, "Data/Map001.rvdata2", "@events/1/@pages/0/@list/4/@parameters/1"
    )
    assert unit.source_text == "勇者"


def test_vxace_extract_skips_comment_and_script_commands(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    locators = {u.locator for u in units if u.file_path == "Data/Map001.rvdata2"}
    prefix = "@events/1/@pages/0/@list"
    assert f"{prefix}/3/@parameters/0" not in locators  # code 108 comment
    assert f"{prefix}/5/@parameters/0" not in locators  # code 355 script


def test_vxace_extract_common_events(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    unit = _by_locator(units, "Data/CommonEvents.rvdata2", "1/@list/0/@parameters/0")
    assert unit.source_text == "共通イベントのテキストです。"


def test_vxace_extract_database_name_nickname_description(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    name = _by_locator(units, "Data/Actors.rvdata2", "1/@name")
    assert name.source_text == "ハロルド"

    nickname = _by_locator(units, "Data/Actors.rvdata2", "1/@nickname")
    assert nickname.source_text == "鍛冶屋"

    description = _by_locator(units, "Data/Actors.rvdata2", "1/@description")
    assert description.source_text == "村の鍛冶屋。"


def test_vxace_extract_skips_pure_tag_note_but_keeps_mixed_note(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    locators = {u.locator for u in units if u.file_path == "Data/Actors.rvdata2"}

    assert "1/@note" not in locators  # actor 1's note は完全に <tag> だけ

    note = _by_locator(units, "Data/Actors.rvdata2", "2/@note")
    assert note.source_text == "実は主人公の姉。<flag:true>"


def test_vxace_extract_skips_empty_fields(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    locators = {(u.file_path, u.locator) for u in units}
    assert ("Data/Actors.rvdata2", "2/@nickname") not in locators
    assert ("Data/Actors.rvdata2", "2/@description") not in locators


def test_vxace_extract_groups_sibling_dialogue_into_same_context_group(vxace_project: Path):
    """不再把兄弟台词整段拼进 context（页面越长开销越是平方级）——改成给同一页面的
    条目打上相同的 context_group，交给 batch_translator 打包进同一次请求整体翻译，
    上下文靠"同一次请求里的其它行"自然获得（调研见 CLAUDE.md）。"""
    units = VXAceAdapter().extract(vxace_project)
    message = _by_locator(
        units, "Data/Map001.rvdata2", "@events/1/@pages/0/@list/0/@parameters/0"
    )
    change_name = _by_locator(
        units, "Data/Map001.rvdata2", "@events/1/@pages/0/@list/4/@parameters/1"
    )
    assert change_name.source_text == "勇者"
    assert message.context == ""
    assert message.context_group
    assert message.context_group == change_name.context_group


def _all_rvdata2_files(root: Path) -> list[Path]:
    return sorted(p.relative_to(root) for p in root.rglob("*.rvdata2"))


def test_m4_roundtrip_untranslated_inject_is_byte_identical(tmp_path: Path, vxace_project: Path):
    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    original_files = _all_rvdata2_files(vxace_project)
    output_files = _all_rvdata2_files(output_dir)
    assert original_files == output_files

    for rel in original_files:
        original_bytes = (vxace_project / rel).read_bytes()
        output_bytes = (output_dir / rel).read_bytes()
        assert output_bytes == original_bytes, f"{rel} differs after untranslated round trip"


def test_vxace_inject_rewraps_translated_paragraph_across_original_line_slots(
    tmp_path: Path, vxace_project: Path
):
    """译文按估算宽度重新分行后，要按顺序塞回原来两行 Show Text 各自的 locator——
    不是简单整段塞进第一行（spec 9.2.a 的简单换行方案）。"""
    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)

    message = _by_locator(
        units, "Data/Map001.rvdata2", "@events/1/@pages/0/@list/0/@parameters/0"
    )
    # 26 个全角字符，超过 DEFAULT_LINE_WIDTH_UNITS=24，必须换到第二行
    message.translated_text = "你好，旅人啊，欢迎来到这个小小的村庄里居住吧"

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    from rpg_translator.codec.rvdata2_codec import read_rvdata2

    translated_map = read_rvdata2(output_dir / "Data" / "Map001.rvdata2")
    page_list = translated_map.attributes["@events"][1].attributes["@pages"][0].attributes["@list"]
    line0 = str(page_list[0].attributes["@parameters"][0])
    line1 = str(page_list[1].attributes["@parameters"][0])

    assert line0 + line1 == message.translated_text
    assert line0 != message.translated_text  # 确认真的拆成了两行，不是塞进第一行完事
    assert line1 != ""


def test_vxace_inject_changes_only_the_targeted_value(tmp_path: Path, vxace_project: Path):
    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)

    target = _by_locator(
        units, "Data/Map001.rvdata2", "@events/1/@pages/0/@list/0/@parameters/0"
    )
    target.translated_text = "TRANSLATED LINE"

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    from rpg_translator.codec.rvdata2_codec import read_rvdata2

    translated_map = read_rvdata2(output_dir / "Data" / "Map001.rvdata2")
    changed = translated_map.attributes["@events"][1].attributes["@pages"][0].attributes["@list"][0]
    assert str(changed.attributes["@parameters"][0]) == "TRANSLATED LINE"

    # 其他文件必须原样不动
    for rel in ["Data/CommonEvents.rvdata2", "Data/Actors.rvdata2"]:
        assert (output_dir / rel).read_bytes() == (vxace_project / rel).read_bytes()


def test_vxace_inject_does_not_mutate_source_project(tmp_path: Path, vxace_project: Path):
    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)
    before = (vxace_project / "Data" / "Map001.rvdata2").read_bytes()

    units[0].translated_text = "whatever"
    adapter.inject(vxace_project, units, tmp_path / "output")

    after = (vxace_project / "Data" / "Map001.rvdata2").read_bytes()
    assert before == after


# ---------------------------------------------------------------------------
# spec 9.2.b：翻译后往 Scripts.rvdata2 注入运行时像素级换行补丁——
# M4.9 用真实 VX Ace 工程（RTP + Game.exe + RGSS301.dll 齐全，可以直接跑）
# 反编译出真实 Window_Base/Window_Message 源码验证过设计（见
# _vxace_message_patch.py 模块文档），这里用手搭的 Scripts.rvdata2 fixture
# 测注入这一步本身的行为，不依赖真实工程（真实工程本身没有提交进仓库）。
# ---------------------------------------------------------------------------

from rpg_translator.engines._vxace_message_patch import RUNTIME_LINE_WRAP_SCRIPT_NAME
from rpg_translator.engines._vxace_scripts import (
    _MARSHAL_HEADER,
    _TAG_ARRAY,
    _write_long,
    encode_new_entry,
    read_scripts,
)


def _write_fake_scripts_file(path: Path, entries: list[tuple[int, str, str]]) -> None:
    body = _MARSHAL_HEADER + bytes([_TAG_ARRAY]) + _write_long(len(entries))
    for script_id, name, source in entries:
        body += encode_new_entry(script_id, name, source)
    path.write_bytes(body)


def test_vxace_after_inject_appends_runtime_patch_when_translated(tmp_path: Path, vxace_project: Path):
    _write_fake_scripts_file(
        vxace_project / "Data" / "Scripts.rvdata2",
        [(1, "Vocab", "module Vocab\nend\n"), (2, "Window_Message", "class Window_Message\nend\n")],
    )

    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)
    units[0].translated_text = "已翻译"

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    entries = read_scripts(output_dir / "Data" / "Scripts.rvdata2")
    assert len(entries) == 3
    assert entries[-1].name == RUNTIME_LINE_WRAP_SCRIPT_NAME
    # 前两个原有脚本必须原样保留，不能因为追加新脚本被重新编码
    original_entries = read_scripts(vxace_project / "Data" / "Scripts.rvdata2")
    assert [(e.id, e.name, e.compressed_source) for e in entries[:2]] == [
        (e.id, e.name, e.compressed_source) for e in original_entries
    ]


def test_vxace_after_inject_skips_patch_when_nothing_translated(tmp_path: Path, vxace_project: Path):
    """纯预览/未翻译的 inject 不该碰 Scripts.rvdata2——保持 M1/M4 的
    "未翻译回填逐字节不变" 回归校验成立。"""
    _write_fake_scripts_file(vxace_project / "Data" / "Scripts.rvdata2", [(1, "Vocab", "module Vocab\nend\n")])

    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)  # 一律不设置 translated_text

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    original_bytes = (vxace_project / "Data" / "Scripts.rvdata2").read_bytes()
    output_bytes = (output_dir / "Data" / "Scripts.rvdata2").read_bytes()
    assert output_bytes == original_bytes


def test_vxace_after_inject_skips_patch_when_conflicting_message_system_present(
    tmp_path: Path, vxace_project: Path
):
    """检测到已知第三方消息系统脚本就跳过注入，降级用估算重排方案兜底
    （spec 9 节的设计决策），不强行覆盖游戏自带的自定义 Window_Message。"""
    _write_fake_scripts_file(
        vxace_project / "Data" / "Scripts.rvdata2",
        [(1, "Vocab", "module Vocab\nend\n"), (2, "YEA-MessageSystem", "class Window_Message\nend\n")],
    )

    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)
    units[0].translated_text = "已翻译"

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    original_bytes = (vxace_project / "Data" / "Scripts.rvdata2").read_bytes()
    output_bytes = (output_dir / "Data" / "Scripts.rvdata2").read_bytes()
    assert output_bytes == original_bytes


def test_vxace_after_inject_skips_patch_when_no_scripts_file(tmp_path: Path, vxace_project: Path):
    """连 Scripts.rvdata2 都没有（比如其他测试用的合成 fixture）就什么都不做，
    不能凭空生出一个原工程没有的文件。"""
    assert not (vxace_project / "Data" / "Scripts.rvdata2").exists()

    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)
    units[0].translated_text = "已翻译"

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    assert not (output_dir / "Data" / "Scripts.rvdata2").exists()
