from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from rpg_translator.core.ir import EngineName, TextUnit, TranslationStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS text_units (
    id TEXT PRIMARY KEY,
    engine TEXT NOT NULL,
    file_path TEXT NOT NULL,
    locator TEXT NOT NULL,
    context TEXT NOT NULL,
    context_group TEXT NOT NULL DEFAULT '',
    source_text TEXT NOT NULL,
    control_code_map TEXT NOT NULL,
    translated_text TEXT,
    status TEXT NOT NULL,
    extra_locators TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS translation_memory (
    source_hash TEXT PRIMARY KEY,
    source_text TEXT NOT NULL,
    translated_text TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """CREATE TABLE IF NOT EXISTS 不会给已存在的旧表补新列，这里手动补，
        兼容 extra_locators 字段加入之前生成的 units.db。"""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(text_units)")}
        if "extra_locators" not in columns:
            self._conn.execute(
                "ALTER TABLE text_units ADD COLUMN extra_locators TEXT NOT NULL DEFAULT '[]'"
            )
        if "context_group" not in columns:
            self._conn.execute(
                "ALTER TABLE text_units ADD COLUMN context_group TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        # update_translation/set_memory 不再各自 commit（见那两个方法的说明），改成
        # 由调用方在合适的粒度上显式 commit()；这里在关闭连接前兜底提交一次剩余的
        # 未提交事务——sqlite3 连接 close() 本身不会自动 commit，直接关掉会连同还没
        # commit 的写入一起丢掉。
        self._conn.commit()
        self._conn.close()

    def commit(self) -> None:
        self._conn.commit()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def upsert_units(self, units: list[TextUnit]) -> None:
        rows = [
            (
                u.id,
                u.engine,
                u.file_path,
                u.locator,
                u.context,
                u.context_group,
                u.source_text,
                json.dumps(u.control_code_map, ensure_ascii=False),
                u.translated_text,
                u.status,
                json.dumps(u.extra_locators, ensure_ascii=False),
            )
            for u in units
        ]
        self._conn.executemany(
            """
            INSERT INTO text_units
                (id, engine, file_path, locator, context, context_group, source_text,
                 control_code_map, translated_text, status, extra_locators)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                engine=excluded.engine,
                file_path=excluded.file_path,
                locator=excluded.locator,
                context=excluded.context,
                context_group=excluded.context_group,
                source_text=excluded.source_text,
                control_code_map=excluded.control_code_map,
                extra_locators=excluded.extra_locators,
                translated_text=CASE
                    WHEN text_units.source_text = excluded.source_text THEN text_units.translated_text
                    ELSE excluded.translated_text
                END,
                status=CASE
                    WHEN text_units.source_text = excluded.source_text THEN text_units.status
                    ELSE excluded.status
                END
            """,
            rows,
        )
        self._conn.commit()

    def get_unit(self, unit_id: str) -> TextUnit | None:
        row = self._conn.execute(
            "SELECT * FROM text_units WHERE id = ?", (unit_id,)
        ).fetchone()
        return self._row_to_unit(row) if row else None

    def list_units(
        self,
        engine: EngineName | None = None,
        status: TranslationStatus | None = None,
    ) -> list[TextUnit]:
        query = "SELECT * FROM text_units WHERE 1=1"
        params: list[str] = []
        if engine is not None:
            query += " AND engine = ?"
            params.append(engine)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_unit(row) for row in rows]

    def update_translation(
        self, unit_id: str, translated_text: str, status: TranslationStatus = "translated"
    ) -> None:
        # 不在这里 commit：批量翻译时一个去重分组可能对应成百上千个 TextUnit（同一句
        # 高频重复短句），逐条 commit 会把 SQLite 的 fsync 开销放大到不成比例——调用方
        # 应该在写完一批相关的行之后自己调 commit()（见 translate/batch_translator.py
        # 的 _write_result），或者依赖 close()/__exit__ 兜底提交。
        self._conn.execute(
            "UPDATE text_units SET translated_text = ?, status = ? WHERE id = ?",
            (translated_text, status, unit_id),
        )

    def get_memory(self, source_hash: str) -> str | None:
        row = self._conn.execute(
            "SELECT translated_text FROM translation_memory WHERE source_hash = ?",
            (source_hash,),
        ).fetchone()
        return row["translated_text"] if row else None

    def set_memory(self, source_hash: str, source_text: str, translated_text: str) -> None:
        # 同 update_translation：不在这里 commit，交给调用方按合适的粒度批量提交。
        self._conn.execute(
            """
            INSERT INTO translation_memory (source_hash, source_text, translated_text)
            VALUES (?, ?, ?)
            ON CONFLICT(source_hash) DO UPDATE SET
                source_text=excluded.source_text,
                translated_text=excluded.translated_text
            """,
            (source_hash, source_text, translated_text),
        )

    @staticmethod
    def _row_to_unit(row: sqlite3.Row) -> TextUnit:
        return TextUnit(
            id=row["id"],
            engine=row["engine"],
            file_path=row["file_path"],
            locator=row["locator"],
            context=row["context"],
            context_group=row["context_group"],
            source_text=row["source_text"],
            control_code_map=json.loads(row["control_code_map"]),
            translated_text=row["translated_text"],
            status=row["status"],
            extra_locators=json.loads(row["extra_locators"]),
        )
