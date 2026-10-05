"""Хранилище на SQLite (WAL). Запросы короткие, поэтому вызываются синхронно."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import catalog
from .colors import ColorInfo
from .stylist import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    first_name    TEXT,
    city          TEXT,
    lat           REAL,
    lon           REAL,
    tz_offset     INTEGER NOT NULL DEFAULT 180,
    notify_hour   INTEGER,
    last_notified TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    category         TEXT,
    sub              TEXT,
    image            TEXT NOT NULL,
    thumb            TEXT NOT NULL,
    colors           TEXT NOT NULL DEFAULT '[]',
    pattern          INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'clean',
    wears_since_wash INTEGER NOT NULL DEFAULT 0,
    wash_after       INTEGER NOT NULL DEFAULT 0,
    times_worn       INTEGER NOT NULL DEFAULT 0,
    last_worn        TEXT,
    favorite         INTEGER NOT NULL DEFAULT 0,
    note             TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_user ON items(user_id, status);
CREATE TABLE IF NOT EXISTS outfits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    item_ids   TEXT NOT NULL,
    score      REAL NOT NULL DEFAULT 0,
    reasons    TEXT NOT NULL DEFAULT '[]',
    status     TEXT NOT NULL DEFAULT 'suggested',
    worn_on    TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outfits_user ON outfits(user_id, status);
CREATE TABLE IF NOT EXISTS pair_weights (
    user_id INTEGER NOT NULL,
    a       INTEGER NOT NULL,
    b       INTEGER NOT NULL,
    w       REAL NOT NULL,
    PRIMARY KEY (user_id, a, b)
);
"""

STATUSES = ("clean", "dirty", "washing", "draft")
EDITABLE = {"category", "sub", "pattern", "wash_after", "favorite", "note", "status"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str, default_tz_offset: int = 180):
        self.default_tz_offset = default_tz_offset
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return cur.lastrowid or cur.rowcount

    # ---------------------------------------------------------------- users
    def ensure_user(self, user_id: int, first_name: str | None = None) -> dict:
        self._x("INSERT OR IGNORE INTO users (id, first_name, tz_offset, created_at) "
                "VALUES (?, ?, ?, ?)", (user_id, first_name, self.default_tz_offset, _now()))
        if first_name:
            self._x("UPDATE users SET first_name=? WHERE id=?", (first_name, user_id))
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: int) -> dict | None:
        rows = self._q("SELECT * FROM users WHERE id=?", (user_id,))
        return dict(rows[0]) if rows else None

    def update_user(self, user_id: int, **fields: Any) -> dict:
        allowed = {"city", "lat", "lon", "tz_offset", "notify_hour", "last_notified", "first_name"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self._x(f"UPDATE users SET {cols} WHERE id=?", (*fields.values(), user_id))
        return self.get_user(user_id)  # type: ignore[return-value]

    def users_with_notifications(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM users WHERE notify_hour IS NOT NULL")]

    # ---------------------------------------------------------------- items
    def add_item(self, user_id: int, *, image: str, thumb: str, colors: list[dict],
                 pattern: bool, category: str | None = None, sub: str | None = None) -> dict:
        status = "clean" if category and sub else "draft"
        wash = catalog.SUB_BY_KEY[sub].wash if sub in catalog.SUB_BY_KEY else 0
        item_id = self._x(
            "INSERT INTO items (user_id, category, sub, image, thumb, colors, pattern, status,"
            " wash_after, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, category, sub, image, thumb, json.dumps(colors, ensure_ascii=False),
             int(pattern), status, wash, _now()))
        return self.get_item(user_id, item_id)  # type: ignore[return-value]

    def get_item(self, user_id: int, item_id: int) -> dict | None:
        rows = self._q("SELECT * FROM items WHERE id=? AND user_id=?", (item_id, user_id))
        return _item_row(rows[0]) if rows else None

    def list_items(self, user_id: int, status: str | None = None) -> list[dict]:
        if status:
            rows = self._q("SELECT * FROM items WHERE user_id=? AND status=? ORDER BY id DESC",
                           (user_id, status))
        else:
            rows = self._q("SELECT * FROM items WHERE user_id=? AND status!='draft' "
                           "ORDER BY id DESC", (user_id,))
        return [_item_row(r) for r in rows]

    def update_item(self, user_id: int, item_id: int, **fields: Any) -> dict | None:
        item = self.get_item(user_id, item_id)
        if not item:
            return None
        fields = {k: v for k, v in fields.items() if k in EDITABLE}
        cat = fields.get("category", item["category"])
        sub = fields.get("sub", item["sub"])
        if "category" in fields or "sub" in fields:
            if not catalog.is_valid(cat, sub):
                raise ValueError("Неизвестная категория или подкатегория")
            if item["sub"] != sub and "wash_after" not in fields:
                fields["wash_after"] = catalog.SUB_BY_KEY[sub].wash
            if item["status"] == "draft" and "status" not in fields:
                fields["status"] = "clean"
        if "status" in fields:
            if fields["status"] not in STATUSES[:3]:
                raise ValueError("Неизвестный статус")
            if not catalog.is_valid(cat, sub):
                raise ValueError("Сначала выберите категорию")
            if fields["status"] == "clean":
                fields["wears_since_wash"] = 0
        for k in ("pattern", "favorite"):
            if k in fields:
                fields[k] = int(bool(fields[k]))
        if "wash_after" in fields:
            fields["wash_after"] = max(0, min(30, int(fields["wash_after"])))
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self._x(f"UPDATE items SET {cols} WHERE id=? AND user_id=?",
                    (*fields.values(), item_id, user_id))
        return self.get_item(user_id, item_id)

    def delete_item(self, user_id: int, item_id: int) -> dict | None:
        item = self.get_item(user_id, item_id)
        if item:
            self._x("DELETE FROM items WHERE id=? AND user_id=?", (item_id, user_id))
            self._x("DELETE FROM pair_weights WHERE user_id=? AND (a=? OR b=?)",
                    (user_id, item_id, item_id))
        return item

    def set_status_bulk(self, user_id: int, from_status: str, to_status: str) -> int:
        extra = ", wears_since_wash=0" if to_status == "clean" else ""
        return self._x(f"UPDATE items SET status=?{extra} WHERE user_id=? AND status=?",
                       (to_status, user_id, from_status))

    def counts(self, user_id: int) -> dict[str, int]:
        rows = self._q("SELECT status, COUNT(*) n FROM items WHERE user_id=? GROUP BY status",
                       (user_id,))
        out = {s: 0 for s in STATUSES}
        out.update({r["status"]: r["n"] for r in rows})
        return out

    def stylist_items(self, user_id: int, include_ids: set[int] | None = None) -> list[Item]:
        """Чистые вещи для генератора (+ явно запрошенные, даже если грязные)."""
        rows = self._q("SELECT * FROM items WHERE user_id=? AND status!='draft'", (user_id,))
        result = []
        for r in rows:
            if r["status"] != "clean" and not (include_ids and r["id"] in include_ids):
                continue
            if not catalog.is_valid(r["category"], r["sub"]):
                continue
            result.append(Item(
                id=r["id"], category=r["category"], sub=r["sub"],
                colors=[ColorInfo(c["hex"], c.get("share", 1)) for c in json.loads(r["colors"])],
                pattern=bool(r["pattern"]), times_worn=r["times_worn"],
                last_worn=date.fromisoformat(r["last_worn"]) if r["last_worn"] else None,
                favorite=bool(r["favorite"])))
        return result

    # -------------------------------------------------------------- outfits
    def save_outfit(self, user_id: int, item_ids: list[int], score: float,
                    reasons: list[str]) -> int:
        return self._x("INSERT INTO outfits (user_id, item_ids, score, reasons, created_at) "
                       "VALUES (?,?,?,?,?)",
                       (user_id, json.dumps(item_ids), score,
                        json.dumps(reasons, ensure_ascii=False), _now()))

    def get_outfit(self, user_id: int, outfit_id: int) -> dict | None:
        rows = self._q("SELECT * FROM outfits WHERE id=? AND user_id=?", (outfit_id, user_id))
        return _outfit_row(rows[0]) if rows else None

    def set_outfit_status(self, user_id: int, outfit_id: int, status: str,
                          worn_on: str | None = None) -> None:
        self._x("UPDATE outfits SET status=?, worn_on=COALESCE(?, worn_on) "
                "WHERE id=? AND user_id=?", (status, worn_on, outfit_id, user_id))

    def history(self, user_id: int, limit: int = 30) -> list[dict]:
        rows = self._q("SELECT * FROM outfits WHERE user_id=? AND status IN ('worn','liked') "
                       "ORDER BY COALESCE(worn_on, created_at) DESC, id DESC LIMIT ?",
                       (user_id, limit))
        return [_outfit_row(r) for r in rows]

    def disliked_sets(self, user_id: int) -> set[frozenset[int]]:
        rows = self._q("SELECT item_ids FROM outfits WHERE user_id=? AND status='disliked'",
                       (user_id,))
        return {frozenset(json.loads(r["item_ids"])) for r in rows}

    def prune_suggestions(self, user_id: int, keep: int = 200) -> None:
        self._x("DELETE FROM outfits WHERE user_id=? AND status='suggested' AND id NOT IN "
                "(SELECT id FROM outfits WHERE user_id=? AND status='suggested' "
                "ORDER BY id DESC LIMIT ?)", (user_id, user_id, keep))

    def wear(self, user_id: int, item_ids: list[int], day: date) -> list[dict]:
        """Отмечает вещи надетыми. Возвращает вещи, которые пора стирать."""
        became_dirty = []
        for item_id in item_ids:
            item = self.get_item(user_id, item_id)
            if not item or item["status"] == "draft":
                continue
            wears = item["wears_since_wash"] + 1
            status = item["status"]
            if item["wash_after"] > 0 and wears >= item["wash_after"] and status == "clean":
                status = "dirty"
                became_dirty.append({**item, "status": status})
            self._x("UPDATE items SET times_worn=times_worn+1, last_worn=?, wears_since_wash=?,"
                    " status=? WHERE id=? AND user_id=?",
                    (day.isoformat(), wears, status, item_id, user_id))
        return became_dirty

    # ------------------------------------------------------------- feedback
    def add_pair_weights(self, user_id: int, deltas: dict[tuple[int, int], float]) -> None:
        for (a, b), d in deltas.items():
            self._x("INSERT INTO pair_weights (user_id, a, b, w) VALUES (?,?,?,?) "
                    "ON CONFLICT(user_id, a, b) DO UPDATE SET w=MAX(-5, MIN(5, w + excluded.w))",
                    (user_id, a, b, d))

    def pair_weights(self, user_id: int) -> dict[tuple[int, int], float]:
        return {(r["a"], r["b"]): r["w"]
                for r in self._q("SELECT a, b, w FROM pair_weights WHERE user_id=?", (user_id,))}


def _item_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["colors"] = json.loads(d["colors"])
    d["pattern"] = bool(d["pattern"])
    d["favorite"] = bool(d["favorite"])
    d["label"] = catalog.label(d["sub"]) if d["sub"] else "Без категории"
    return d


def _outfit_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["item_ids"] = json.loads(d["item_ids"])
    d["reasons"] = json.loads(d["reasons"])
    return d
