"""The safety layer every tool call passes through.

Policy (V1, agreed 2026-09-05):
  * Reads: any account the token can see, optionally restricted to
    META_READ_ACCOUNT_IDS.
  * Writes: only accounts in META_WRITE_ACCOUNT_IDS (the sandbox), never
    above MAX_DAILY_BUDGET_MINOR, and every write asks the operator y/n.
  * Everything is appended to logs/actions.jsonl, refused calls included.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Settings


@dataclass
class ApprovalRequest:
    """What the operator is asked to approve. `text` is the ready-to-show
    summary; the structured fields let a UI render it its own way."""

    action: str
    account_id: str
    details: dict[str, Any]
    text: str
    money: list[str] = field(default_factory=list)


Approver = Callable[[ApprovalRequest], bool]
EventSink = Callable[[dict[str, Any]], None]


class Guard:
    def __init__(self, settings: Settings, approver: Approver, log_path: Path | None = None,
                 on_event: EventSink | None = None) -> None:
        self.settings = settings
        self.approver = approver
        self.log_path = log_path or settings.log_path
        self.on_event = on_event

    # -- policy ------------------------------------------------------------

    def check_read(self, account_id: str) -> str | None:
        allowed = self.settings.read_accounts
        if allowed and account_id not in allowed:
            return (f"Policy: reading {account_id} is not allowed. "
                    f"Readable accounts: {', '.join(allowed)}.")
        return None

    def authorize_write(self, action: str, account_id: str, details: dict[str, Any]) -> str | None:
        """Return None if the write may proceed, else the reason it may not."""
        if account_id not in self.settings.write_accounts:
            reason = (f"Policy: writes are only allowed on the sandbox account(s) "
                      f"{', '.join(self.settings.write_accounts) or '(none configured)'}; "
                      f"{account_id} is read-only in this version.")
            self.log(action, account_id, details, approved=False, note=reason)
            return reason

        for key in ("daily_budget", "lifetime_budget"):
            value = details.get(key)
            if value is not None and int(value) > self.settings.max_daily_budget_minor:
                reason = (f"Policy: {key}={value} exceeds the cap of "
                          f"{self.settings.max_daily_budget_minor} minor units.")
                self.log(action, account_id, details, approved=False, note=reason)
                return reason

        prompt = f"{action} on {account_id}\n{json.dumps(details, indent=2, ensure_ascii=False)}"
        money = [f"{k.replace('_', ' ')}: {int(v) / 100:,.2f} in account currency ({v} minor units)"
                 for k, v in details.items() if k.endswith("_budget") and v is not None]
        if money:
            prompt += "\n\n" + "\n".join(money)
        if not self.approver(ApprovalRequest(action, account_id, details, prompt, money)):
            reason = "Operator declined this action."
            self.log(action, account_id, details, approved=False, note=reason)
            return reason

        self.log(action, account_id, details, approved=True)
        return None

    # -- audit -------------------------------------------------------------

    def log(self, action: str, account_id: str | None, details: dict[str, Any],
            approved: bool | None = None, note: str | None = None) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action,
            "account": account_id,
            "details": details,
            "approved": approved,
            "note": note,
            "dry_run": self.settings.dry_run,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if self.on_event is not None:
            self.on_event(entry)

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        """Last n audit entries, newest last."""
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
