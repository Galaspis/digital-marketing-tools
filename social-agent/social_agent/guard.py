"""Policy layer for anything that leaves the building.

  * Drafts are free: they only touch our database.
  * Publishing, scheduling and cancelling on the platform need (a) the
    target to be in SOCIAL_PUBLISH_TARGETS, (b) the daily cap not to be
    exceeded, (c) the operator's approval — from the chat approval card,
    or implicitly when the operator clicks Publish in the UI themselves.
  * Everything is appended to logs/actions.jsonl.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Settings


@dataclass
class ApprovalRequest:
    action: str
    target_id: str
    details: dict[str, Any]
    text: str
    summary: list[str] = field(default_factory=list)


Approver = Callable[[ApprovalRequest], bool]
EventSink = Callable[[dict[str, Any]], None]


class Guard:
    def __init__(self, settings: Settings, approver: Approver, count_recent: Callable[[str], int],
                 log_path: Path | None = None, on_event: EventSink | None = None) -> None:
        self.settings = settings
        self.approver = approver
        self.count_recent = count_recent  # posts published since <iso>
        self.log_path = log_path or settings.log_path
        self.on_event = on_event

    # -- policy ------------------------------------------------------------

    def check_target(self, target_id: str) -> str | None:
        if target_id not in self.settings.publish_targets:
            return (f"Policy: publishing to {target_id} is not enabled. Allowed targets: "
                    f"{', '.join(self.settings.publish_targets) or '(none configured)'}.")
        return None

    def check_cap(self) -> str | None:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        n = self.count_recent(since)
        if n >= self.settings.max_posts_per_day:
            return (f"Policy: {n} posts already published in the last 24 h; "
                    f"the cap is {self.settings.max_posts_per_day} (SOCIAL_MAX_POSTS_PER_DAY).")
        return None

    def authorize(self, action: str, target_id: str, details: dict[str, Any],
                  summary: list[str] | None = None, operator_initiated: bool = False) -> str | None:
        """None = go ahead. Otherwise the reason it may not."""
        for why in (self.check_target(target_id), self.check_cap() if action in ("publish", "schedule") else None):
            if why:
                self.log(action, target_id, details, approved=False, note=why)
                return why
        if operator_initiated:
            self.log(action, target_id, details, approved=True, note="operator clicked in the UI")
            return None
        text = f"{action} -> {target_id}\n{json.dumps(details, indent=2, ensure_ascii=False)}"
        if summary:
            text += "\n\n" + "\n".join(summary)
        if not self.approver(ApprovalRequest(action, target_id, details, text, summary or [])):
            self.log(action, target_id, details, approved=False, note="Operator declined this action.")
            return "Operator declined this action."
        self.log(action, target_id, details, approved=True)
        return None

    # -- audit -------------------------------------------------------------

    def log(self, action: str, target_id: str | None, details: dict[str, Any],
            approved: bool | None = None, note: str | None = None) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action, "target": target_id, "details": details,
            "approved": approved, "note": note, "dry_run": self.settings.dry_run,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if self.on_event is not None:
            self.on_event(entry)

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
