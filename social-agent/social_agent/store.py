"""SQLite store for posts. One table, plain dicts in and out.

Status lifecycle:
  draft -> scheduled -> publishing -> published
        \\-> publishing -> published            (publish now)
  scheduled -> cancelled ; publishing -> failed (error kept on the row)

`scheduler` says who fires a scheduled post: "native" (the platform holds
it, e.g. Facebook's own scheduling) or "local" (our Scheduler thread, used
where the platform cannot schedule, e.g. Instagram).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POST_KINDS = ("text", "link", "photo", "video", "reel", "story", "carousel")
STATUSES = ("draft", "scheduled", "publishing", "published", "failed", "cancelled")
TARGET_TYPES = ("facebook_page", "instagram")

_COLUMNS = [
    "id", "channel", "target_id", "target_name", "target_type", "kind", "text", "link",
    "media", "scheduled_at", "scheduler", "status", "remote_id", "permalink", "error",
    "source", "created_at", "updated_at", "published_at",
]
_JSON_COLUMNS = {"media"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_utc_iso(value: str) -> str:
    """Parse an ISO-8601 string (naive = local time) and return UTC ISO."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret as local time
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS posts ("
            "id TEXT PRIMARY KEY, channel TEXT, target_id TEXT, target_name TEXT, target_type TEXT,"
            " kind TEXT, text TEXT, link TEXT, media TEXT, scheduled_at TEXT, scheduler TEXT,"
            " status TEXT, remote_id TEXT, permalink TEXT, error TEXT, source TEXT,"
            " created_at TEXT, updated_at TEXT, published_at TEXT)"
        )
        self._db.commit()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        d = dict(r)
        for k in _JSON_COLUMNS:
            d[k] = json.loads(d[k]) if d.get(k) else []
        return d

    @staticmethod
    def _encode(fields: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in fields.items():
            if k not in _COLUMNS:
                raise KeyError(f"unknown column {k}")
            out[k] = json.dumps(v, ensure_ascii=False) if k in _JSON_COLUMNS else v
        return out

    # -- CRUD --------------------------------------------------------------

    def create(self, **fields: Any) -> dict[str, Any]:
        fields.setdefault("id", uuid.uuid4().hex[:10])
        fields.setdefault("status", "draft")
        fields.setdefault("media", [])
        fields.setdefault("channel", "meta")
        fields.setdefault("source", "agent")
        ts = now_iso()
        fields.setdefault("created_at", ts)
        fields["updated_at"] = ts
        enc = self._encode(fields)
        cols = ", ".join(enc)
        marks = ", ".join("?" for _ in enc)
        with self._lock:
            self._db.execute(f"INSERT INTO posts ({cols}) VALUES ({marks})", list(enc.values()))
            self._db.commit()
        return self.get(fields["id"])  # type: ignore[return-value]

    def update(self, post_id: str, **fields: Any) -> dict[str, Any]:
        fields["updated_at"] = now_iso()
        enc = self._encode(fields)
        sets = ", ".join(f"{k} = ?" for k in enc)
        with self._lock:
            cur = self._db.execute(f"UPDATE posts SET {sets} WHERE id = ?", [*enc.values(), post_id])
            self._db.commit()
        if cur.rowcount == 0:
            raise KeyError(f"post {post_id} not found")
        return self.get(post_id)  # type: ignore[return-value]

    def get(self, post_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._db.execute("SELECT * FROM posts WHERE id = ?", [post_id]).fetchone())

    def delete(self, post_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM posts WHERE id = ?", [post_id])
            self._db.commit()

    def list(self, status: str | None = None, target_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, args = [], []
        if status:
            where.append("status = ?"); args.append(status)
        if target_id:
            where.append("target_id = ?"); args.append(target_id)
        sql = "SELECT * FROM posts" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY COALESCE(scheduled_at, created_at) DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [self._row(r) for r in self._db.execute(sql, args).fetchall()]  # type: ignore[misc]

    def due(self, now: str | None = None) -> list[dict[str, Any]]:
        """Locally scheduled posts whose time has come."""
        now = now or now_iso()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM posts WHERE status = 'scheduled' AND scheduler = 'local' "
                "AND scheduled_at <= ? ORDER BY scheduled_at", [now]).fetchall()
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def native_pending(self) -> list[dict[str, Any]]:
        """Platform-scheduled posts we should check on once their time has passed."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM posts WHERE status = 'scheduled' AND scheduler = 'native' "
                "AND scheduled_at <= ?", [now_iso()]).fetchall()
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def published_since(self, since_iso: str) -> int:
        with self._lock:
            (n,) = self._db.execute(
                "SELECT COUNT(*) FROM posts WHERE status IN ('published', 'publishing') "
                "AND COALESCE(published_at, updated_at) >= ?", [since_iso]).fetchone()
        return int(n)
