from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

# PySide6 需要一个平台插件；测试机大多没有真实显示环境，用 offscreen。
# 必须在任何 PySide6 导入之前设置。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_JSON_DUMP_KWARGS: dict[str, Any] = {"ensure_ascii": False, "separators": (",", ":")}
_qsettings_tmp_dir = tempfile.TemporaryDirectory(prefix="rpg_translator_qsettings_")


def _isolate_qsettings_from_real_registry() -> None:
    """QSettings(org, app) 在 Windows 上默认写系统注册表（NativeFormat）。测试不应该
    碰用户真实的注册表，这里强制改成 ini 文件格式，路径指到一个临时目录。"""
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _qsettings_tmp_dir.name)


_isolate_qsettings_from_real_registry()


def _make_in_memory_keyring():
    """set_deepseek_api_key() 会真的写 Windows 凭据管理器。测试不该碰用户真实的
    系统凭据存储，这里换成一个纯内存的假 keyring backend。"""
    from keyring.backend import KeyringBackend

    class _InMemoryKeyring(KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def __init__(self) -> None:
            super().__init__()
            self._store: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self._store.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self._store[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self._store.pop((service, username), None)

    return _InMemoryKeyring()


@pytest.fixture(autouse=True)
def _fake_keyring_backend():
    """每个测试都换一个全新的空白假 backend——避免某个测试写入的假凭据
    （比如 SettingsDialog 测试里存的假 API Key）泄漏到其他测试，把真实
    .env 里配置的 key 顶掉。"""
    import keyring

    keyring.set_keyring(_make_in_memory_keyring())


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, **_JSON_DUMP_KWARGS), encoding="utf-8")


def _sample_page_list() -> list[dict[str, Any]]:
    return [
        {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2, "ハロルド"]},
        {"code": 401, "indent": 0, "parameters": ["こんにちは、旅人よ。"]},
        {"code": 401, "indent": 0, "parameters": ["この村へようこそ。"]},
        {"code": 102, "indent": 0, "parameters": [["はい", "いいえ"], 1, 0, 2, 0]},
        {"code": 108, "indent": 0, "parameters": ["plugin:config=1"]},
        {"code": 320, "indent": 0, "parameters": [1, "勇者"]},
        {"code": 324, "indent": 0, "parameters": [1, "剣士"]},
        {"code": 325, "indent": 0, "parameters": [1, "村を守る剣士。"]},
        {"code": 355, "indent": 0, "parameters": ["console.log('ok');"]},
        {"code": 0, "indent": 0, "parameters": []},
    ]


def _build_data_files(data_dir: Path, *, is_mz: bool) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)

    system: dict[str, Any] = {
        "gameTitle": "Test Game",
        "encryptionKey": "",
        "hasEncryptedImages": False,
        "hasEncryptedMusic": False,
    }
    if is_mz:
        system["locale"] = "ja_JP"
    _write_json(data_dir / "System.json", system)

    _write_json(
        data_dir / "MapInfos.json",
        [None, {"id": 1, "expanded": False, "name": "テストマップ", "order": 1, "parentId": 0}],
    )

    page_list = _sample_page_list()
    if not is_mz:
        # MV の 101 にはスピーカー名パラメータが無い、324/325 も未対応
        page_list[0] = {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2]}
        page_list = [cmd for cmd in page_list if cmd["code"] not in (324, 325)]

    map001 = {
        "autoplayBgm": False,
        "autoplayBse": False,
        "battleback1Name": "",
        "battleback2Name": "",
        "bgm": {"name": "", "pan": 0, "pitch": 100, "volume": 90},
        "bgs": {"name": "", "pan": 0, "pitch": 100, "volume": 90},
        "disableDashing": False,
        "displayName": "",
        "encounterList": [],
        "encounterStep": 30,
        "height": 5,
        "note": "",
        "parallaxLoopX": False,
        "parallaxLoopY": False,
        "parallaxName": "",
        "parallaxShow": True,
        "parallaxSx": 0,
        "parallaxSy": 0,
        "scrollType": 0,
        "specifyBattleback": False,
        "tilesetId": 1,
        "width": 5,
        "data": [0] * (5 * 5 * 6),
        "events": [
            None,
            {
                "id": 1,
                "name": "EV001",
                "note": "",
                "pages": [
                    {
                        "conditions": {"actorId": 1, "actorValid": False, "itemId": 1, "itemValid": False},
                        "directionFix": False,
                        "image": {"characterIndex": 0, "characterName": "", "direction": 2, "pattern": 0, "tileId": 0},
                        "list": page_list,
                        "moveFrequency": 3,
                        "moveRoute": {"list": [{"code": 0}], "repeat": True, "skippable": False, "wait": False},
                        "moveSpeed": 3,
                        "moveType": 0,
                        "priorityType": 1,
                        "stepAnime": False,
                        "through": False,
                        "trigger": 0,
                        "walkAnime": True,
                    }
                ],
                "x": 1,
                "y": 1,
            },
            None,  # 削除済みイベントスロット（null は extract 側でスキップされる想定）
        ],
        "meta": {},
    }
    _write_json(data_dir / "Map001.json", map001)

    common_events = [
        None,
        {
            "id": 1,
            "list": [
                {"code": 401, "indent": 0, "parameters": ["共通イベントのテキストです。"]},
                {"code": 108, "indent": 0, "parameters": ["comment only"]},
                {"code": 0, "indent": 0, "parameters": []},
            ],
            "name": "CE001",
            "switchId": 1,
            "trigger": 0,
        },
    ]
    _write_json(data_dir / "CommonEvents.json", common_events)

    actors = [
        None,
        {
            "id": 1,
            "name": "ハロルド",
            "nickname": "鍛冶屋",
            "classId": 1,
            "note": "<param:1>\n<hidden>",
            "profile": "村の鍛冶屋。",
            "description": "",
        },
        {
            "id": 2,
            "name": "アリス",
            "nickname": "",
            "classId": 2,
            "note": "実は主人公の姉。<flag:true>",
            "profile": "",
            "description": "",
        },
    ]
    _write_json(data_dir / "Actors.json", actors)


def build_mz_project(root: Path) -> Path:
    project_dir = root / "mz_project"
    _build_data_files(project_dir / "data", is_mz=True)
    return project_dir


def build_mv_project(root: Path) -> Path:
    project_dir = root / "mv_project"
    _build_data_files(project_dir / "www" / "data", is_mz=False)
    return project_dir


def build_vxace_project(root: Path) -> Path:
    from rubymarshal.classes import RubyObject
    from rubymarshal.writer import writes

    def _write_rv(path: Path, obj: Any) -> None:
        path.write_bytes(writes(obj))

    data_dir = root / "vxace_project" / "Data"
    data_dir.mkdir(parents=True, exist_ok=True)

    page_list = [
        RubyObject("RPG::EventCommand", {"@code": 401, "@indent": 0, "@parameters": ["こんにちは、旅人よ。"]}),
        RubyObject("RPG::EventCommand", {"@code": 401, "@indent": 0, "@parameters": ["この村へようこそ。"]}),
        RubyObject(
            "RPG::EventCommand",
            {"@code": 102, "@indent": 0, "@parameters": [["はい", "いいえ"], 1, 0, 2, 0]},
        ),
        RubyObject("RPG::EventCommand", {"@code": 108, "@indent": 0, "@parameters": ["plugin:config=1"]}),
        RubyObject("RPG::EventCommand", {"@code": 320, "@indent": 0, "@parameters": [1, "勇者"]}),
        RubyObject("RPG::EventCommand", {"@code": 355, "@indent": 0, "@parameters": ["puts 'ok'"]}),
        RubyObject("RPG::EventCommand", {"@code": 0, "@indent": 0, "@parameters": []}),
    ]
    page = RubyObject(
        "RPG::Event::Page",
        {
            "@condition": RubyObject("RPG::Event::Page::Condition", {}),
            "@graphic": RubyObject("RPG::Event::Page::Graphic", {}),
            "@move_type": 0,
            "@move_speed": 3,
            "@move_frequency": 3,
            "@move_route": RubyObject("RPG::MoveRoute", {"@list": [], "@repeat": True}),
            "@walk_anime": True,
            "@step_anime": False,
            "@direction_fix": False,
            "@through": False,
            "@priority_type": 1,
            "@trigger": 0,
            "@list": page_list,
        },
    )
    event = RubyObject("RPG::Event", {"@id": 1, "@name": "EV001", "@x": 1, "@y": 1, "@pages": [page]})
    game_map = RubyObject(
        "RPG::Map",
        {
            "@tileset_id": 1,
            "@width": 5,
            "@height": 5,
            "@scroll_type": 0,
            "@specify_battleback": False,
            "@battleback1_name": "",
            "@battleback2_name": "",
            "@autoplay_bgm": False,
            "@bgm": RubyObject("RPG::BGM", {}),
            "@autoplay_bgs": False,
            "@bgs": RubyObject("RPG::BGS", {}),
            "@disable_dashing": False,
            "@encounter_list": [],
            "@encounter_step": 30,
            "@parallax_name": "",
            "@parallax_loop_x": False,
            "@parallax_loop_y": False,
            "@parallax_sx": 0,
            "@parallax_sy": 0,
            "@parallax_show": True,
            "@note": "",
            "@data": [0] * (5 * 5 * 3),
            "@events": {1: event},
            "@display_name": "",
        },
    )
    _write_rv(data_dir / "Map001.rvdata2", game_map)

    common_events = [
        None,
        RubyObject(
            "RPG::CommonEvent",
            {
                "@id": 1,
                "@name": "CE001",
                "@trigger": 0,
                "@switch_id": 1,
                "@list": [
                    RubyObject(
                        "RPG::EventCommand",
                        {"@code": 401, "@indent": 0, "@parameters": ["共通イベントのテキストです。"]},
                    ),
                    RubyObject(
                        "RPG::EventCommand", {"@code": 108, "@indent": 0, "@parameters": ["comment only"]}
                    ),
                    RubyObject("RPG::EventCommand", {"@code": 0, "@indent": 0, "@parameters": []}),
                ],
            },
        ),
    ]
    _write_rv(data_dir / "CommonEvents.rvdata2", common_events)

    actors = [
        None,
        RubyObject(
            "RPG::Actor",
            {
                "@id": 1,
                "@name": "ハロルド",
                "@nickname": "鍛冶屋",
                "@class_id": 1,
                "@note": "<param:1>\n<hidden>",
                "@description": "村の鍛冶屋。",
                "@icon_index": 0,
                "@features": [],
                "@initial_level": 1,
                "@max_level": 99,
                "@character_name": "",
                "@character_index": 0,
                "@face_name": "",
                "@face_index": 0,
                "@equips": [0, 0, 0, 0, 0],
            },
        ),
        RubyObject(
            "RPG::Actor",
            {
                "@id": 2,
                "@name": "アリス",
                "@nickname": "",
                "@class_id": 2,
                "@note": "実は主人公の姉。<flag:true>",
                "@description": "",
                "@icon_index": 0,
                "@features": [],
                "@initial_level": 1,
                "@max_level": 99,
                "@character_name": "",
                "@character_index": 0,
                "@face_name": "",
                "@face_index": 0,
                "@equips": [0, 0, 0, 0, 0],
            },
        ),
    ]
    _write_rv(data_dir / "Actors.rvdata2", actors)

    return data_dir.parent


def build_wolf_project(root: Path) -> Path:
    """Hand-built synthetic WOLF RPG Editor project, matching the byte layout
    documented in rpg_translator.engines.wolf_binary (itself cross-checked
    against three independent community reverse-engineering efforts -- see
    that module's docstring). No real WOLF project was available to diff
    against for this milestone, so this fixture is the only thing
    tests/test_engines_wolf.py can validate the parser/serializer against;
    it deliberately exercises: Message/Choices text extraction, a
    Comment command that must NOT be extracted (mirrors this codebase's
    existing "skip comment commands" convention), a Move command carrying an
    embedded route list (to prove that structural path round-trips even
    though it holds no translatable text), a page-level route list, a
    CommonEvent with all of its "unknown"/opaque blocks populated with
    non-trivial (not all-zero) values (to prove they round-trip byte-exact
    rather than accidentally passing because they were empty), and a
    Database type exercising the "skip empty string", "skip string
    containing a newline" and "skip type!=0 string field" extraction
    heuristics side by side.
    """
    from rpg_translator.engines import wolf_binary as wb

    project_dir = root / "wolf_project"
    data_dir = project_dir / "Data"
    basic_data_dir = data_dir / "BasicData"
    map_data_dir = data_dir / "MapData"
    basic_data_dir.mkdir(parents=True, exist_ok=True)
    map_data_dir.mkdir(parents=True, exist_ok=True)

    # --- Data/MapData/Map001.mps ------------------------------------------
    width, height = 5, 5
    move_command = wb.Command(
        cid=201,
        args=[0, 0],
        indent=0,
        string_args=[],
        move_extra=wb.MoveExtra(
            unknown=[1, 2, 3, 4, 5],
            flags=7,
            route=[wb.RouteCommand(command_id=1, args=[0])],
        ),
    )
    page = wb.Page(
        unknown1=0,
        graphic_name="",
        graphic_direction=2,
        graphic_frame=0,
        graphic_opacity=255,
        graphic_render_mode=0,
        conditions=bytes(wb._CONDITIONS_SIZE),
        movement=bytes(wb._MOVEMENT_SIZE),
        flags=0,
        route_flags=0,
        route=[wb.RouteCommand(command_id=2, args=[3, 4])],
        commands=[
            wb.Command(cid=101, args=[0, 0, 0], indent=0, string_args=["こんにちは、旅人よ。"]),
            wb.Command(cid=101, args=[0, 0, 0], indent=0, string_args=["この村へようこそ。"]),
            wb.Command(cid=102, args=[1, 0, 2, 0], indent=0, string_args=["はい", "いいえ"]),
            wb.Command(cid=103, args=[], indent=0, string_args=["plugin:config=1"]),
            move_command,
            wb.Command(cid=0, args=[], indent=0, string_args=[]),
        ],
        shadow_graphic_num=0,
        collision_width=0,
        collision_height=0,
    )
    event = wb.Event(event_id=0, name="EV001", x=1, y=1, pages=[page])
    game_map = wb.WolfMap(
        tileset_id=1,
        width=width,
        height=height,
        tiles=bytes(width * height * 3 * 4),
        events=[event],
        header_stamp="なし",
    )
    game_map.write(map_data_dir / "Map001.mps")

    # --- Data/BasicData/CommonEvent.dat ------------------------------------
    common_event = wb.CommonEvent(
        event_id=0,
        unknown1=1,
        unknown2=bytes([1, 2, 3, 4, 5, 6, 7]),
        name="CE001",
        commands=[
            wb.Command(cid=101, args=[0, 0, 0], indent=0, string_args=["共通イベントのテキストです。"]),
            wb.Command(cid=103, args=[], indent=0, string_args=["comment only"]),
            wb.Command(cid=0, args=[], indent=0, string_args=[]),
        ],
        unknown11="stamp11",
        description="テスト用共通イベント",
        unknown3=[f"u3-{i}" for i in range(10)],
        unknown4=[i for i in range(10)],
        unknown5=[[f"u5-{i}-{j}" for j in range(i % 3)] for i in range(10)],
        unknown6=[[i, i + 1] if i % 2 == 0 else [] for i in range(10)],
        unknown7=bytes(range(0x1D)),
        unknown8=[f"u8-{i}" if i < 3 else "" for i in range(100)],
        unknown9="u9",
        unknown10="u10",
        unknown12=42,
    )
    common_events = wb.WolfCommonEvents(events=[common_event])
    common_events.write(basic_data_dir / "CommonEvent.dat")

    # --- Data/BasicData/DataBase.project + DataBase.dat --------------------
    field_name = wb.Field(name="名前", type=0, index_info=wb._FIELD_STRING_START + 0)
    field_level = wb.Field(name="レベル", type=0, index_info=wb._FIELD_INT_START + 0)
    field_desc = wb.Field(name="説明", type=0, index_info=wb._FIELD_STRING_START + 1)
    field_ref = wb.Field(name="参照名", type=1, index_info=wb._FIELD_STRING_START + 2)
    fields = [field_name, field_level, field_desc, field_ref]

    record_harold = wb.DataRecord(
        name="0",
        int_values=[5],
        string_values=["ハロルド", "村の鍛冶屋。", "normal"],
    )
    record_alice = wb.DataRecord(
        name="1",
        int_values=[1],
        string_values=["アリス", "", "hidden"],  # empty description -> not extracted
    )
    record_multiline = wb.DataRecord(
        name="2",
        int_values=[10],
        string_values=["剣士", "1行目\n2行目", "normal"],  # newline -> not extracted
    )

    actors_type = wb.DbType(
        name="Actors",
        fields=fields,
        data=[record_harold, record_alice, record_multiline],
        description="アクター定義",
        field_type_list_size=8,
    )
    database = wb.WolfDatabase(types=[actors_type])
    database.write(basic_data_dir / "DataBase.project", basic_data_dir / "DataBase.dat")

    return project_dir


@pytest.fixture
def wolf_project(tmp_path: Path) -> Path:
    return build_wolf_project(tmp_path)


def build_wolf_project_v35(root: Path) -> Path:
    """手搭的 "v3.5" 格式 WOLF RPG Editor 工程——对应 M4.9 用真实 WOLF RPG
    Editor v3.712 自带示例工程验证后发现的格式（LZ4 块压缩正文、Map 头部
    多两个 int、Page 的 features/page_transfer、每条 Command 末尾的 v3.5
    尾部数据块）。真实工程本身没有提交进仓库（见 wolf_binary.py 模块文档
    "真实工程验证" 一节），所以这份 fixture 是这条格式分支唯一的回归测试
    依据；专门覆盖 build_wolf_project() 那份经典格式 fixture 不会触发的
    分支：LZ4 压缩/解压、features > 3 时的 page_transfer 字段、Command 的
    v35_unknown 尾部字节、Database Type 的 unknown2 哨兵字段。
    """
    from rpg_translator.engines import wolf_binary as wb

    project_dir = root / "wolf_project_v35"
    data_dir = project_dir / "Data"
    basic_data_dir = data_dir / "BasicData"
    map_data_dir = data_dir / "MapData"
    basic_data_dir.mkdir(parents=True, exist_ok=True)
    map_data_dir.mkdir(parents=True, exist_ok=True)

    # --- Data/MapData/Map001.mps (v3.5, LZ4 压缩, UTF-8) -------------------
    width, height, layer_cnt = 4, 4, 3
    page = wb.Page(
        unknown1=0,
        graphic_name="",
        graphic_direction=2,
        graphic_frame=0,
        graphic_opacity=255,
        graphic_render_mode=0,
        conditions=bytes(wb._CONDITIONS_SIZE),
        movement=bytes(wb._MOVEMENT_SIZE),
        flags=0,
        route_flags=0,
        route=[],
        commands=[
            wb.Command(
                cid=101,
                args=[0, 0, 0],
                indent=0,
                string_args=["こんにちは、v3.5！"],
                v35_unknown=bytes([9, 9, 9]),
            ),
            wb.Command(cid=101, args=[0, 0, 0], indent=0, string_args=["二行目のテキスト。"]),
        ],
        shadow_graphic_num=0,
        collision_width=0,
        collision_height=0,
        features=5,  # > 3 -> page_transfer 字段必须存在并且能回填
        page_transfer=7,
    )
    event = wb.Event(event_id=0, name="EV001", x=1, y=1, pages=[page])
    game_map = wb.WolfMap(
        tileset_id=1,
        width=width,
        height=height,
        tiles=bytes(width * height * layer_cnt * 4),
        events=[event],
        header_stamp="なし",
        version=0x67,
        unknown2=0x69,
        unknown4=3,
        layer_cnt=layer_cnt,
        is_utf8=True,
    )
    game_map.write(map_data_dir / "Map001.mps")

    # --- Data/BasicData/CommonEvent.dat (v3.5, LZ4 压缩) --------------------
    common_event = wb.CommonEvent(
        event_id=0,
        unknown1=1,
        unknown2=bytes([1, 2, 3, 4, 5, 6, 7]),
        name="CE001",
        commands=[
            wb.Command(
                cid=101,
                args=[0, 0, 0],
                indent=0,
                string_args=["v3.5 共通イベントのテキスト。"],
                v35_unknown=bytes([1, 2]),
            ),
        ],
        unknown11="stamp11",
        description="v3.5テスト",
        unknown3=[f"u3-{i}" for i in range(10)],
        unknown4=[i for i in range(10)],
        unknown5=[[] for _ in range(10)],
        unknown6=[[] for _ in range(10)],
        unknown7=bytes(range(0x1D)),
        unknown8=["" for _ in range(100)],
        unknown9="u9",
    )
    common_events = wb.WolfCommonEvents(events=[common_event], version=0x93, is_utf8=True)
    common_events.write(basic_data_dir / "CommonEvent.dat")

    # --- Data/BasicData/DataBase.project + DataBase.dat (v3.5, LZ4 压缩) ---
    field_name = wb.Field(name="名前", type=0, index_info=wb._FIELD_STRING_START + 0)
    record = wb.DataRecord(name="0", int_values=[], string_values=["ハロルド"])
    actors_type = wb.DbType(
        name="Actors",
        fields=[field_name],
        data=[record],
        description="v3.5アクター定義",
        field_type_list_size=1,
    )
    database = wb.WolfDatabase(types=[actors_type], version=0xC4, is_utf8=True)
    database.write(basic_data_dir / "DataBase.project", basic_data_dir / "DataBase.dat")

    return project_dir


@pytest.fixture
def wolf_project_v35(tmp_path: Path) -> Path:
    return build_wolf_project_v35(tmp_path)


@pytest.fixture
def vxace_project(tmp_path: Path) -> Path:
    return build_vxace_project(tmp_path)


def build_xp_project(root: Path) -> Path:
    """手搭的 RPG Maker XP 合成工程——和 build_vxace_project 结构基本一致，
    但关键差异是文本字段用 `bytes` 而不是 `str`：M4.9 用真实 XP 工程（GitHub
    上的 GPL-3.0 开源同人游戏 torresflo/Pokemon-Obsidian）实测发现，XP 用的
    老版本 Ruby（1.8，字符串没有编码感知）marshal 出来的字符串，`rubymarshal`
    读出来原样是 `bytes`，不会像 VX Ace（Ruby 1.9+，ivar 编码标记）那样自动
    解码——之前代码对这类值直接调用 Python `str()`，抽出来的"文本"其实是
    `b'...'` 这种 repr 字面量，完全不能用（见 _rgss_common.py 的
    `rv_str`/`_encode_like`）。这份 fixture 特意用 `bytes` 值复现真实格式，
    保证这个真机 bug 有回归测试兜底，不用每次都靠下载真实工程才能测出来。
    """
    from rubymarshal.classes import RubyObject
    from rubymarshal.writer import writes

    def _write_rv(path: Path, obj: Any) -> None:
        path.write_bytes(writes(obj))

    data_dir = root / "xp_project" / "Data"
    data_dir.mkdir(parents=True, exist_ok=True)

    page_list = [
        RubyObject("RPG::EventCommand", {"@code": 401, "@indent": 0, "@parameters": [b"Bonjour, voyageur."]}),
        RubyObject("RPG::EventCommand", {"@code": 401, "@indent": 0, "@parameters": [b"Bienvenue au village."]}),
        RubyObject(
            "RPG::EventCommand",
            {"@code": 102, "@indent": 0, "@parameters": [[b"Oui", b"Non"], 1, 0, 2, 0]},
        ),
        RubyObject("RPG::EventCommand", {"@code": 108, "@indent": 0, "@parameters": [b"plugin:config=1"]}),
        RubyObject("RPG::EventCommand", {"@code": 320, "@indent": 0, "@parameters": [1, b"Heros"]}),
        RubyObject("RPG::EventCommand", {"@code": 0, "@indent": 0, "@parameters": []}),
    ]
    page = RubyObject(
        "RPG::Event::Page",
        {
            "@condition": RubyObject("RPG::Event::Page::Condition", {}),
            "@graphic": RubyObject("RPG::Event::Page::Graphic", {}),
            "@move_type": 0,
            "@move_speed": 3,
            "@move_frequency": 3,
            "@move_route": RubyObject("RPG::MoveRoute", {"@list": [], "@repeat": True}),
            "@walk_anime": True,
            "@step_anime": False,
            "@direction_fix": False,
            "@through": False,
            "@priority_type": 1,
            "@trigger": 0,
            "@list": page_list,
        },
    )
    event = RubyObject("RPG::Event", {"@id": 1, "@name": b"EV001", "@x": 1, "@y": 1, "@pages": [page]})
    game_map = RubyObject(
        "RPG::Map",
        {
            "@tileset_id": 1,
            "@width": 5,
            "@height": 5,
            "@autoplay_bgm": False,
            "@bgm": RubyObject("RPG::AudioFile", {}),
            "@autoplay_bgs": False,
            "@bgs": RubyObject("RPG::AudioFile", {}),
            "@encounter_list": [],
            "@encounter_step": 30,
            "@data": [0] * (5 * 5 * 3),
            "@events": {1: event},
        },
    )
    _write_rv(data_dir / "Map001.rxdata", game_map)

    common_events = [
        None,
        RubyObject(
            "RPG::CommonEvent",
            {
                "@id": 1,
                "@name": b"CE001",
                "@trigger": 0,
                "@switch_id": 1,
                "@list": [
                    RubyObject(
                        "RPG::EventCommand",
                        {"@code": 401, "@indent": 0, "@parameters": [b"Texte d'evenement commun."]},
                    ),
                    RubyObject("RPG::EventCommand", {"@code": 0, "@indent": 0, "@parameters": []}),
                ],
            },
        ),
    ]
    _write_rv(data_dir / "CommonEvents.rxdata", common_events)

    actors = [
        None,
        RubyObject(
            "RPG::Actor",
            {
                "@id": 1,
                "@name": b"Rouge",
                "@description": b"Un jeune dresseur plein d'espoir.",
            },
        ),
    ]
    _write_rv(data_dir / "Actors.rxdata", actors)

    for name in ("Classes", "Skills", "Items", "Weapons", "Armors", "Enemies", "States"):
        _write_rv(data_dir / f"{name}.rxdata", [None])

    (root / "xp_project" / "Game.rxproj").write_bytes(b"")

    return root / "xp_project"


@pytest.fixture
def xp_project(tmp_path: Path) -> Path:
    return build_xp_project(tmp_path)


def build_vx_project(root: Path) -> Path:
    """跟 build_xp_project 结构一样（VX 和 XP 是同一个老版本 Ruby 系列，字符串
    同样是 bytes，M4.9 用真实 VX 工程 ambratolm-games/flower-in-pain 验证过），
    只是文件后缀换成 VX 自己的 `.rvdata`（VX Ace 才是 `.rvdata2`）。"""
    from rubymarshal.classes import RubyObject
    from rubymarshal.writer import writes

    def _write_rv(path: Path, obj: Any) -> None:
        path.write_bytes(writes(obj))

    data_dir = root / "vx_project" / "Data"
    data_dir.mkdir(parents=True, exist_ok=True)

    page_list = [
        RubyObject("RPG::EventCommand", {"@code": 401, "@indent": 0, "@parameters": [b"Bonjour."]}),
        RubyObject("RPG::EventCommand", {"@code": 0, "@indent": 0, "@parameters": []}),
    ]
    page = RubyObject(
        "RPG::Event::Page",
        {
            "@condition": RubyObject("RPG::Event::Page::Condition", {}),
            "@graphic": RubyObject("RPG::Event::Page::Graphic", {}),
            "@move_type": 0,
            "@move_speed": 3,
            "@move_frequency": 3,
            "@move_route": RubyObject("RPG::MoveRoute", {"@list": [], "@repeat": True}),
            "@walk_anime": True,
            "@step_anime": False,
            "@direction_fix": False,
            "@through": False,
            "@priority_type": 1,
            "@trigger": 0,
            "@list": page_list,
        },
    )
    event = RubyObject("RPG::Event", {"@id": 1, "@name": b"EV001", "@x": 1, "@y": 1, "@pages": [page]})
    game_map = RubyObject(
        "RPG::Map",
        {
            "@tileset_id": 1,
            "@width": 5,
            "@height": 5,
            "@autoplay_bgm": False,
            "@bgm": RubyObject("RPG::AudioFile", {}),
            "@autoplay_bgs": False,
            "@bgs": RubyObject("RPG::AudioFile", {}),
            "@encounter_list": [],
            "@encounter_step": 30,
            "@data": [0] * (5 * 5 * 3),
            "@events": {1: event},
        },
    )
    _write_rv(data_dir / "Map001.rvdata", game_map)

    actors = [None, RubyObject("RPG::Actor", {"@id": 1, "@name": b"Rouge"})]
    _write_rv(data_dir / "Actors.rvdata", actors)

    for name in ("Classes", "Skills", "Items", "Weapons", "Armors", "Enemies", "States", "CommonEvents"):
        _write_rv(data_dir / f"{name}.rvdata", [None])

    (root / "vx_project" / "Game.rvproj").write_bytes(b"")

    return root / "vx_project"


@pytest.fixture
def vx_project(tmp_path: Path) -> Path:
    return build_vx_project(tmp_path)


@pytest.fixture
def mz_project(tmp_path: Path) -> Path:
    return build_mz_project(tmp_path)


@pytest.fixture
def mv_project(tmp_path: Path) -> Path:
    return build_mv_project(tmp_path)
