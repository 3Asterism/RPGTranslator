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


@pytest.fixture
def vxace_project(tmp_path: Path) -> Path:
    return build_vxace_project(tmp_path)


@pytest.fixture
def mz_project(tmp_path: Path) -> Path:
    return build_mz_project(tmp_path)


@pytest.fixture
def mv_project(tmp_path: Path) -> Path:
    return build_mv_project(tmp_path)
