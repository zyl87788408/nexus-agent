"""Local long-term memory backed by SQLite.

Fully offline: a single file, no server, no network. A FTS5 index provides
keyword retrieval when available; otherwise a LIKE fallback keeps search
working, so behaviour never depends on the SQLite build.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path


class MemoryStore:
    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._fts = False
        self._init_schema()

    # ---------------------------------------------------------------- schema
    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'note',
                tags TEXT NOT NULL DEFAULT '[]',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_kind ON memories(kind)")
        try:
            cur.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                "USING fts5(id UNINDEXED, content, tags)"
            )
            self._fts = True
        except sqlite3.OperationalError:
            self._fts = False
        self._conn.commit()

    # ------------------------------------------------------------------ CRUD
    def add(self, content: str, *, kind: str = "note", tags: list[str] | None = None,
            metadata: dict | None = None, importance: float = 0.5) -> str:
        if not str(content).strip():
            raise ValueError("memory content must not be empty")
        memory_id = uuid.uuid4().hex
        now = time.time()
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        self._conn.execute(
            "INSERT INTO memories (id, content, kind, tags, metadata, created_at,"
            " updated_at, importance) VALUES (?,?,?,?,?,?,?,?)",
            (memory_id, content, kind, tags_json,
             json.dumps(metadata or {}, ensure_ascii=False), now, now, importance),
        )
        if self._fts:
            self._conn.execute(
                "INSERT INTO memories_fts (id, content, tags) VALUES (?,?,?)",
                (memory_id, content, " ".join(tags or [])),
            )
        self._conn.commit()
        return memory_id

    def get(self, memory_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return self._row(row) if row else None

    def update(self, memory_id: str, *, content: str | None = None,
               tags: list[str] | None = None, metadata: dict | None = None,
               importance: float | None = None) -> bool:
        existing = self.get(memory_id)
        if not existing:
            return False
        new_content = content if content is not None else existing["content"]
        new_tags = tags if tags is not None else existing["tags"]
        new_meta = metadata if metadata is not None else existing["metadata"]
        new_imp = importance if importance is not None else existing["importance"]
        self._conn.execute(
            "UPDATE memories SET content=?, tags=?, metadata=?, importance=?,"
            " updated_at=? WHERE id=?",
            (new_content, json.dumps(new_tags, ensure_ascii=False),
             json.dumps(new_meta, ensure_ascii=False), new_imp, time.time(),
             memory_id),
        )
        if self._fts:
            self._conn.execute(
                "UPDATE memories_fts SET content=?, tags=? WHERE id=?",
                (new_content, " ".join(new_tags), memory_id),
            )
        self._conn.commit()
        return True

    def delete(self, memory_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        if self._fts:
            self._conn.execute("DELETE FROM memories_fts WHERE id = ?", (memory_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # --------------------------------------------------------------- queries
    def search(self, query: str, *, limit: int = 10, kind: str | None = None) -> list[dict]:
        if not str(query).strip():
            return []
        rows = []
        if self._fts:
            sql = (
                "SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.id "
                "WHERE memories_fts MATCH ?"
            )
            params: list = [self._fts_query(query)]
            if kind:
                sql += " AND m.kind = ?"
                params.append(kind)
            sql += " ORDER BY m.importance DESC, m.updated_at DESC LIMIT ?"
            params.append(limit)
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            sql = "SELECT * FROM memories WHERE content LIKE ?"
            params = [f"%{query}%"]
            if kind:
                sql += " AND kind = ?"
                params.append(kind)
            sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int = 10, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM memories"
        params: list = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [self._row(r) for r in self._conn.execute(sql, params).fetchall()]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def context_for(self, query: str, *, limit: int = 5) -> str:
        """Render retrieved memories as a prompt fragment."""
        hits = self.search(query, limit=limit)
        if not hits:
            return ""
        lines = [f"- {h['content']}" for h in hits]
        return "Relevant remembered context:\n" + "\n".join(lines)

    # ---------------------------------------------------------------- backup
    def backup(self, destination: Path | str) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._conn.commit()
        shutil.copy2(self.path, target)
        return target

    @classmethod
    def restore(cls, backup_path: Path | str, db_path: Path | str) -> "MemoryStore":
        source = Path(backup_path)
        if not source.exists():
            raise FileNotFoundError(source)
        target = Path(db_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return cls(target)

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- helpers
    def _row(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["tags"] = json.loads(data.get("tags") or "[]")
        data["metadata"] = json.loads(data.get("metadata") or "{}")
        return data

    @staticmethod
    def _fts_query(query: str) -> str:
        # Quote each token so user input cannot inject FTS operators.
        tokens = [t for t in query.replace('"', " ").split() if t]
        return " OR ".join(f'"{t}"' for t in tokens) or '""'
