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
    source_text TEXT NOT NULL,
    control_code_map TEXT NOT NULL,
    translated_text TEXT,
    status TEXT NOT NULL
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
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

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
                u.source_text,
                json.dumps(u.control_code_map, ensure_ascii=False),
                u.translated_text,
                u.status,
            )
            for u in units
        ]
        self._conn.executemany(
            """
            INSERT INTO text_units
                (id, engine, file_path, locator, context, source_text,
                 control_code_map, translated_text, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                engine=excluded.engine,
                file_path=excluded.file_path,
                locator=excluded.locator,
                context=excluded.context,
                source_text=excluded.source_text,
                control_code_map=excluded.control_code_map,
                translated_text=excluded.translated_text,
                status=excluded.status
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
        self._conn.execute(
            "UPDATE text_units SET translated_text = ?, status = ? WHERE id = ?",
            (translated_text, status, unit_id),
        )
        self._conn.commit()

    def get_memory(self, source_hash: str) -> str | None:
        row = self._conn.execute(
            "SELECT translated_text FROM translation_memory WHERE source_hash = ?",
            (source_hash,),
        ).fetchone()
        return row["translated_text"] if row else None

    def set_memory(self, source_hash: str, source_text: str, translated_text: str) -> None:
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
        self._conn.commit()

    @staticmethod
    def _row_to_unit(row: sqlite3.Row) -> TextUnit:
        return TextUnit(
            id=row["id"],
            engine=row["engine"],
            file_path=row["file_path"],
            locator=row["locator"],
            context=row["context"],
            source_text=row["source_text"],
            control_code_map=json.loads(row["control_code_map"]),
            translated_text=row["translated_text"],
            status=row["status"],
        )
