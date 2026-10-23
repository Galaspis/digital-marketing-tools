"""One ledger for every paid action across all services.

A row is written the moment money is spent: a chat turn, an image, a research
run, later an ad spend snapshot. Rows carry what they belonged to (post,
campaign, turn) so "what did this post cost, end to end" is a sum.

SQLite at <hub>/data/ledger.sqlite; the services share the file. Volumes are
tiny, so one connection per process with a lock is enough.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pricing import HUB_ROOT

DEFAULT_DB = HUB_ROOT / "data" / "ledger.sqlite"

COLUMNS = ("ts", "month", "service", "client", "action", "provider", "model", "input_tokens", "output_tokens",
           "cache_read", "cache_write", "images", "usd", "ref_type", "ref_id", "post_id", "campaign_id", "turn_id", "note")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_DB
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS costs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, month TEXT,"
            " service TEXT, client TEXT, action TEXT, provider TEXT, model TEXT,"
            " input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_read INTEGER DEFAULT 0,"
            " cache_write INTEGER DEFAULT 0, images INTEGER DEFAULT 0, usd REAL DEFAULT 0,"
            " ref_type TEXT, ref_id TEXT, post_id TEXT, campaign_id TEXT, turn_id TEXT, note TEXT)")
        self._db.execute("CREATE INDEX IF NOT EXISTS costs_month ON costs(month)")
        self._db.execute("CREATE INDEX IF NOT EXISTS costs_post ON costs(post_id)")
        self._db.commit()

    # -- write -------------------------------------------------------------

    def record(self, *, service: str, action: str, provider: str, model: str, usd: float, client: str = "agency",
               input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0, cache_write: int = 0,
               images: int = 0, ref_type: str | None = None, ref_id: str | None = None,
               post_id: str | None = None, campaign_id: str | None = None, turn_id: str | None = None,
               note: str | None = None, ts: datetime | None = None) -> int:
        ts = ts or _now()
        row = (ts.isoformat(timespec="seconds"), ts.strftime("%Y-%m"), service, client, action, provider, model,
               int(input_tokens or 0), int(output_tokens or 0), int(cache_read or 0), int(cache_write or 0),
               int(images or 0), round(float(usd or 0), 6), ref_type, ref_id, post_id, campaign_id, turn_id, note)
        with self._lock:
            cur = self._db.execute(f"INSERT INTO costs ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", row)
            self._db.commit()
            return int(cur.lastrowid)

    def attach_post(self, post_id: str, *, ref_ids: list[str] | tuple[str, ...] = (), turn_id: str | None = None) -> int:
        """Point earlier rows (an image by file/url, or a whole turn) at a post."""
        n = 0
        with self._lock:
            if ref_ids:
                marks = ",".join("?" * len(ref_ids))
                n += self._db.execute(f"UPDATE costs SET post_id = ? WHERE post_id IS NULL AND ref_id IN ({marks})",
                                      [post_id, *ref_ids]).rowcount
            if turn_id:
                n += self._db.execute("UPDATE costs SET post_id = ? WHERE post_id IS NULL AND turn_id = ?",
                                      [post_id, turn_id]).rowcount
            self._db.commit()
        return n

    def attach_campaign(self, campaign_id: str, *, turn_id: str) -> int:
        with self._lock:
            n = self._db.execute("UPDATE costs SET campaign_id = ? WHERE campaign_id IS NULL AND turn_id = ?",
                                 [campaign_id, turn_id]).rowcount
            self._db.commit()
        return n

    # -- read --------------------------------------------------------------

    @staticmethod
    def this_month() -> str:
        return _now().strftime("%Y-%m")

    def month_total(self, month: str | None = None) -> float:
        month = month or self.this_month()
        with self._lock:
            (v,) = self._db.execute("SELECT COALESCE(SUM(usd), 0) FROM costs WHERE month = ?", [month]).fetchone()
        return round(float(v), 4)

    def _group(self, month: str, *cols: str) -> list[dict[str, Any]]:
        sel = ", ".join(cols)
        sql = (f"SELECT {sel}, COUNT(*) AS n, SUM(usd) AS usd, SUM(input_tokens + cache_read + cache_write) AS tokens_in,"
               f" SUM(output_tokens) AS tokens_out, SUM(images) AS images FROM costs WHERE month = ? GROUP BY {sel} ORDER BY usd DESC")
        with self._lock:
            rows = self._db.execute(sql, [month]).fetchall()
        return [{**dict(r), "usd": round(r["usd"] or 0, 4)} for r in rows]

    def summary(self, month: str | None = None) -> dict[str, Any]:
        month = month or self.this_month()
        with self._lock:
            months = [r[0] for r in self._db.execute("SELECT DISTINCT month FROM costs ORDER BY month DESC").fetchall()]
            per_post = self._db.execute(
                "SELECT post_id, COUNT(*) AS n, SUM(usd) AS usd, SUM(images) AS images, MAX(ts) AS last "
                "FROM costs WHERE month = ? AND post_id IS NOT NULL GROUP BY post_id ORDER BY usd DESC LIMIT 50", [month]).fetchall()
            per_campaign = self._db.execute(
                "SELECT campaign_id, COUNT(*) AS n, SUM(usd) AS usd, MAX(ts) AS last "
                "FROM costs WHERE month = ? AND campaign_id IS NOT NULL GROUP BY campaign_id ORDER BY usd DESC LIMIT 50", [month]).fetchall()
            by_day = self._db.execute(
                "SELECT substr(ts, 1, 10) AS day, SUM(usd) AS usd, COUNT(*) AS n FROM costs WHERE month = ? GROUP BY day ORDER BY day", [month]).fetchall()
        return {
            "month": month, "months": months, "total": self.month_total(month),
            "by_model": self._group(month, "provider", "model"),
            "by_service": self._group(month, "service"),
            "by_client": self._group(month, "client"),
            "by_action": self._group(month, "action"),
            "by_day": [{**dict(r), "usd": round(r["usd"] or 0, 4)} for r in by_day],
            "per_post": [{**dict(r), "usd": round(r["usd"] or 0, 4)} for r in per_post],
            "per_campaign": [{**dict(r), "usd": round(r["usd"] or 0, 4)} for r in per_campaign],
        }

    def rows(self, month: str | None = None, post_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, args = [], []
        if month:
            where.append("month = ?"); args.append(month)
        if post_id:
            where.append("post_id = ?"); args.append(post_id)
        sql = "SELECT * FROM costs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def post_costs(self, post_ids: list[str]) -> dict[str, float]:
        if not post_ids:
            return {}
        marks = ",".join("?" * len(post_ids))
        with self._lock:
            rows = self._db.execute(f"SELECT post_id, SUM(usd) AS usd FROM costs WHERE post_id IN ({marks}) GROUP BY post_id",
                                    list(post_ids)).fetchall()
        return {r["post_id"]: round(r["usd"] or 0, 4) for r in rows}
