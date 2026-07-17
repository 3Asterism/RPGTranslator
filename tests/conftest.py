from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_JSON_DUMP_KWARGS: dict[str, Any] = {"ensure_ascii": False, "separators": (",", ":")}


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


@pytest.fixture
def mz_project(tmp_path: Path) -> Path:
    return build_mz_project(tmp_path)


@pytest.fixture
def mv_project(tmp_path: Path) -> Path:
    return build_mv_project(tmp_path)
