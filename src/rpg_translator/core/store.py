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
        # 默认的 rollback journal 模式下，每次 commit() 都要对主数据库文件做一次
        # fsync；批量翻译时 batch_translator._write_result 按 job（去重分组）粒度
        # commit，大工程下是几千到几万次这样的调用，且这些调用是在 asyncio 事件
        # 循环线程里同步执行的（sqlite3 没有 await 版本），每次 fsync 期间会连带
        # 卡住其它并发批次的调度、GUI 侧进度/日志信号的处理。WAL 模式把提交写进
        # 一个只追加的 WAL 文件（不需要为每次提交同步主文件），配合
        # synchronous=NORMAL（WAL 模式下官方推荐的搭配）能把这部分 fsync 开销降
        # 一个数量级，代价仅仅是"操作系统本身崩溃"这种极端情况下可能丢最后几次
        # 提交（应用崩溃/断电但系统还活着的常规场景下 WAL 依然保证已提交数据不丢），
        # 对翻译记忆这类可重新生成的本地缓存数据完全可以接受。
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
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

    def list_unit_ids(self, exclude_status: TranslationStatus | None = None) -> set[str]:
        """只取 id 列，不构造完整 TextUnit（跳过 control_code_map/extra_locators 两个
        JSON 字段的解析）——GUI 拖入工程时用来算"已翻译 X/Y"续译提示只需要知道哪些
        id 不是 pending，之前借道 list_units() 整表读出来再在 Python 里过滤，大工程
        （几万行）下要为每一行多余地跑两次 json.loads，只是为了扔掉结果。"""
        query = "SELECT id FROM text_units"
        params: list[str] = []
        if exclude_status is not None:
            query += " WHERE status != ?"
            params.append(exclude_status)
        rows = self._conn.execute(query, params).fetchall()
        return {row["id"] for row in rows}

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

    def delete_missing(self, keep_ids: set[str]) -> int:
        """删掉 text_units 里所有 id 不在 keep_ids 集合中的历史行——用于"重新提取一遍
        当前游戏文本，把已经不再对应任何现存文本的旧行清掉"（见 pipeline.prune_stale_units）。
        keep_ids 为空几乎不可能是"这工程真的一条文本都没有"，更可能是提取意外失败/传参
        出错，这里直接拒绝，防止把整张表清空。返回实际删除的行数。"""
        if not keep_ids:
            raise ValueError("keep_ids 不能为空，拒绝清空整张 text_units 表")
        rows = self._conn.execute("SELECT id FROM text_units").fetchall()
        stale_ids = [row["id"] for row in rows if row["id"] not in keep_ids]
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            self._conn.execute(
                f"DELETE FROM text_units WHERE id IN ({placeholders})", stale_ids
            )
            self._conn.commit()
        return len(stale_ids)

    def vacuum(self) -> None:
        """回收 delete_missing 之类的删除操作腾出的磁盘空间——sqlite 默认不会自动
        收缩文件体积，DELETE 之后文件本身不会变小，需要显式 VACUUM 才会真正瘦身。"""
        self._conn.execute("VACUUM")

    def delete_missing(self, keep_ids: set[str]) -> int:
        """删掉 text_units 里所有 id 不在 keep_ids 集合中的历史行——用于"重新提取一遍
        当前游戏文本，把已经不再对应任何现存文本的旧行清掉"（见 pipeline.prune_stale_units）。
        keep_ids 为空几乎不可能是"这工程真的一条文本都没有"，更可能是提取意外失败/传参
        出错，这里直接拒绝，防止把整张表清空。返回实际删除的行数。"""
        if not keep_ids:
            raise ValueError("keep_ids 不能为空，拒绝清空整张 text_units 表")
        rows = self._conn.execute("SELECT id FROM text_units").fetchall()
        stale_ids = [row["id"] for row in rows if row["id"] not in keep_ids]
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            self._conn.execute(
                f"DELETE FROM text_units WHERE id IN ({placeholders})", stale_ids
            )
            self._conn.commit()
        return len(stale_ids)

    def vacuum(self) -> None:
        """回收 delete_missing 之类的删除操作腾出的磁盘空间——sqlite 默认不会自动
        收缩文件体积，DELETE 之后文件本身不会变小，需要显式 VACUUM 才会真正瘦身。"""
        self._conn.execute("VACUUM")

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
