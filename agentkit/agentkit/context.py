"""Per-turn context so tools deep in a chat turn can say which post or
campaign they touched. The chat thread sets it at the start of a turn; the
ledger uses it to attach the turn's cost to what it produced.

contextvars are per thread here: each turn runs in its own thread and sets
the context itself, so nothing leaks between turns."""

from __future__ import annotations

import contextvars
from typing import Any

_turn: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("agentkit_turn", default=None)


def begin_turn(turn_id: str, client: str = "agency", service: str = "") -> dict[str, Any]:
    ctx = {"turn_id": turn_id, "client": client, "service": service, "post_ids": [], "campaign_ids": []}
    _turn.set(ctx)
    return ctx


def current_turn() -> dict[str, Any] | None:
    return _turn.get()


def note_post(post_id: str) -> None:
    ctx = _turn.get()
    if ctx is not None and post_id not in ctx["post_ids"]:
        ctx["post_ids"].append(post_id)


def note_campaign(campaign_id: str) -> None:
    ctx = _turn.get()
    if ctx is not None and campaign_id not in ctx["campaign_ids"]:
        ctx["campaign_ids"].append(campaign_id)
