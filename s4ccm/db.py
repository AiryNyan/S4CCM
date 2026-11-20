from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    rel_path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    auto_category TEXT,
    user_set INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    reason TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    members TEXT,
    thumb TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS items_fp ON items(fingerprint);
CREATE TABLE IF NOT EXISTS notes (
    fingerprint TEXT PRIMARY KEY,
    memo TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    user_thumb TEXT
);
CREATE TABLE IF NOT EXISTS ops (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    label TEXT NOT NULL,
    moves TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS res_ids (fp TEXT NOT NULL, inst INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS res_ids_fp ON res_ids(fp);
CREATE TABLE IF NOT EXISTS remembered (
    fingerprint TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    user_set INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL
);
"""


def _signed(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def _unsigned(v: int) -> int:
    return v + (1 << 64) if v < 0 else v


@dataclass
class Item:
    id: int
    fingerprint: str
    rel_path: str
    kind: str
    name: str
    category: str
    auto_category: str | None
    user_set: bool
    confidence: float
    reason: str
    size: int
    mtime: float
    added_at: float
    status: str
    members: list[str] = field(default_factory=list)
    thumb: str | None = None
    meta: dict = field(default_factory=dict)
    memo: str = ""
    tags: str = ""
    source_url: str = ""
    user_thumb: str | None = None

    @property
    def display_thumb(self) -> str | None:
        return self.user_thumb or self.thumb


class DB:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _row_to_item(self, r: sqlite3.Row) -> Item:
        return Item(
            id=r["id"], fingerprint=r["fingerprint"], rel_path=r["rel_path"], kind=r["kind"],
            name=r["name"], category=r["category"], auto_category=r["auto_category"],
            user_set=bool(r["user_set"]), confidence=r["confidence"], reason=r["reason"] or "",
            size=r["size"], mtime=r["mtime"], added_at=r["added_at"], status=r["status"],
            members=json.loads(r["members"]) if r["members"] else [],
            thumb=r["thumb"], meta=json.loads(r["meta"]) if r["meta"] else {},
            memo=r["memo"] or "", tags=r["tags"] or "", source_url=r["source_url"] or "",
            user_thumb=r["user_thumb"],
        )

    _SELECT = ("SELECT i.*, n.memo, n.tags, n.source_url, n.user_thumb FROM items i "
               "LEFT JOIN notes n ON n.fingerprint = i.fingerprint")

    def all_items(self) -> list[Item]:
        with self._lock:
            return [self._row_to_item(r) for r in self.conn.execute(self._SELECT + " ORDER BY i.name COLLATE NOCASE")]

    def get(self, item_id: int) -> Item | None:
        with self._lock:
            r = self.conn.execute(self._SELECT + " WHERE i.id=?", (item_id,)).fetchone()
            return self._row_to_item(r) if r else None

    def by_path(self) -> dict[str, sqlite3.Row]:
        with self._lock:
            return {r["rel_path"]: r for r in self.conn.execute("SELECT * FROM items")}

    def by_fingerprint(self, fp: str) -> list[Item]:
        with self._lock:
            return [self._row_to_item(r) for r in self.conn.execute(self._SELECT + " WHERE i.fingerprint=?", (fp,))]

    def upsert(self, **f) -> None:
        f.setdefault("added_at", time.time())
        if isinstance(f.get("members"), list):
            f["members"] = json.dumps(f["members"], ensure_ascii=False)
        if isinstance(f.get("meta"), dict):
            f["meta"] = json.dumps(f["meta"], ensure_ascii=False)
        cols = ", ".join(f)
        qs = ", ".join("?" for _ in f)
        upd = ", ".join(f"{k}=excluded.{k}" for k in f if k not in ("rel_path", "added_at"))
        with self._lock:
            self.conn.execute(f"INSERT INTO items ({cols}) VALUES ({qs}) "
                              f"ON CONFLICT(rel_path) DO UPDATE SET {upd}", list(f.values()))

    def update_item(self, item_id: int, **f) -> None:
        if isinstance(f.get("members"), list):
            f["members"] = json.dumps(f["members"], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in f)
        with self._lock:
            self.conn.execute(f"UPDATE items SET {sets} WHERE id=?", [*f.values(), item_id])
            self.conn.commit()

    def rename_path(self, old_rel: str, new_rel: str, **extra) -> None:
        if isinstance(extra.get("members"), list):
            extra["members"] = json.dumps(extra["members"], ensure_ascii=False)
        with self._lock:
            self.conn.execute("DELETE FROM items WHERE rel_path=? AND rel_path<>?", (new_rel, old_rel))
            sets = ", ".join(["rel_path=?"] + [f"{k}=?" for k in extra])
            self.conn.execute(f"UPDATE items SET {sets} WHERE rel_path=?", [new_rel, *extra.values(), old_rel])
            self.conn.commit()

    def delete_paths(self, rels: list[str]) -> None:
        with self._lock:
            self.conn.executemany("DELETE FROM items WHERE rel_path=?", [(r,) for r in rels])
            self.conn.commit()

    def remember_paths(self, rels: list[str]) -> None:
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO remembered (fingerprint, category, user_set, added_at) "
                "SELECT fingerprint, category, user_set, added_at FROM items "
                "WHERE rel_path=? AND kind='package'", [(r,) for r in rels])

    def recall(self, fingerprint: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute("SELECT * FROM remembered WHERE fingerprint=?", (fingerprint,)).fetchone()

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def set_note(self, fingerprint: str, **f) -> None:
        with self._lock:
            self.conn.execute("INSERT OR IGNORE INTO notes (fingerprint) VALUES (?)", (fingerprint,))
            sets = ", ".join(f"{k}=?" for k in f)
            self.conn.execute(f"UPDATE notes SET {sets} WHERE fingerprint=?", [*f.values(), fingerprint])
            self.conn.commit()

    def log_op(self, label: str, moves: list[tuple[str, str]]) -> int:
        with self._lock:
            cur = self.conn.execute("INSERT INTO ops (at, label, moves) VALUES (?,?,?)",
                                    (time.time(), label, json.dumps(moves, ensure_ascii=False)))
            self.conn.commit()
            return cur.lastrowid

    def open_ops(self, label_prefix: str) -> list[tuple[int, str, list[list[str]]]]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM ops WHERE undone=0 AND substr(label, 1, ?)=? ORDER BY id DESC",
                                     (len(label_prefix), label_prefix)).fetchall()
            return [(r["id"], r["label"], json.loads(r["moves"])) for r in rows]

    def mark_undone(self, op_id: int) -> None:
        with self._lock:
            self.conn.execute("UPDATE ops SET undone=1 WHERE id=?", (op_id,))
            self.conn.commit()

    def kv_get(self, k: str, default=None):
        with self._lock:
            r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return json.loads(r["v"]) if r else default

    def set_res_ids(self, fp: str, ids) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM res_ids WHERE fp=?", (fp,))
            self.conn.executemany("INSERT INTO res_ids (fp, inst) VALUES (?, ?)", [(fp, _signed(i)) for i in set(ids)])

    def res_id_map(self) -> dict[int, set[str]]:
        out: dict[int, set[str]] = {}
        with self._lock:
            for fp, inst in self.conn.execute("SELECT fp, inst FROM res_ids"):
                out.setdefault(_unsigned(inst), set()).add(fp)
        return out

    def kv_set(self, k: str, v) -> None:
        with self._lock:
            self.conn.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                              (k, json.dumps(v, ensure_ascii=False)))
            self.conn.commit()
