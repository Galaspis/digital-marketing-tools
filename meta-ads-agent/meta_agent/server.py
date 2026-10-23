"""Local HTTP API for the web UI. Run with `python serve.py` (from the
meta-ads-agent folder) or `npm run meta` from the hub root.

One operator, one conversation, one turn at a time. A chat turn runs in a
background thread; the page polls /api/turn. When a write tool asks for
approval, the turn blocks until POST /api/approve arrives (or 10 minutes
pass, which counts as a decline).

Only binds to 127.0.0.1. The hub's Express server proxies /api/meta/* here.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Callable

from flask import Flask, jsonify, request

from agentkit import Ledger, PriceBook, begin_turn
from agentkit.ledger_api import make_blueprint

from .config import Settings, load_settings, normalise_account_id
from .guard import ApprovalRequest, Guard
from .meta_client import DEFAULT_INSIGHT_FIELDS, OBJECT_TYPES, MetaBackend, MetaError, make_backend
from .tools import DATE_PRESETS, build_tools

APPROVAL_TIMEOUT_SECONDS = 600


class Busy(Exception):
    pass


class Session:
    """Conversation state shared between the agent thread and HTTP handlers."""

    def __init__(self, settings: Settings, ledger: Ledger | None = None, service: str = "meta-ads") -> None:
        self.settings = settings
        self.ledger = ledger
        self.service = service
        self.lock = threading.Lock()
        self.turns: list[dict[str, Any]] = []
        self.turn: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None
        self._pending_event: threading.Event | None = None
        self.agent = None  # set by attach()

    def attach(self, agent) -> None:
        self.agent = agent
        agent.on_text = lambda text: self._event({"type": "text", "text": text})
        agent.on_tool_call = lambda name, inp: self._event({"type": "tool", "name": name, "input": inp})

    # -- events ------------------------------------------------------------

    def _event(self, event: dict[str, Any]) -> None:
        with self.lock:
            if self.turn is not None and self.turn["status"] == "running":
                event.setdefault("ts", time.time())
                self.turn["events"].append(event)

    def on_log(self, entry: dict[str, Any]) -> None:
        # Only refusals are interesting in the chat stream; reads already show as tool chips.
        if entry.get("approved") is False and entry.get("note"):
            self._event({"type": "refused", "action": entry["action"], "note": entry["note"]})

    # -- approvals ---------------------------------------------------------

    def approver(self, req: ApprovalRequest) -> bool:
        pending = {
            "type": "approval", "id": uuid.uuid4().hex[:10], "action": req.action,
            "account": req.account_id, "details": req.details, "text": req.text,
            "money": req.money, "decision": None, "ts": time.time(),
        }
        event = threading.Event()
        with self.lock:
            self.pending = pending
            self._pending_event = event
            if self.turn is not None:
                self.turn["events"].append(pending)  # same dict: decision shows up in place
        answered = event.wait(APPROVAL_TIMEOUT_SECONDS)
        with self.lock:
            if not answered:
                pending["decision"] = False
                pending["timed_out"] = True
            self.pending = None
            self._pending_event = None
            return bool(pending["decision"])

    def decide(self, approval_id: str, approve: bool) -> bool:
        with self.lock:
            if self.pending is None or self.pending["id"] != approval_id:
                return False
            self.pending["decision"] = bool(approve)
            self.pending["decided_at"] = time.time()
            assert self._pending_event is not None
            self._pending_event.set()
            return True

    # -- turns -------------------------------------------------------------

    def start_turn(self, text: str, model: str | None = None) -> dict[str, Any]:
        with self.lock:
            if self.turn is not None and self.turn["status"] == "running":
                raise Busy()
            turn = {"id": uuid.uuid4().hex[:10], "text": text, "status": "running",
                    "events": [], "reply": None, "usage": None, "error": None,
                    "started": time.time(), "finished": None, "model": model or self.settings.model}
            self.turn = turn
            self.turns.append(turn)
        threading.Thread(target=self._run, args=(turn, text, model), daemon=True).start()
        return turn

    def _run(self, turn: dict[str, Any], text: str, model: str | None = None) -> None:
        ctx = begin_turn(turn["id"], client=self.settings.client, service=self.service)
        try:
            reply, usage = self.agent.send(text, model=model)
            used_model = getattr(self.agent, "last_model", None) or model or self.settings.model
            usd = round(usage.usd(used_model), 4)
            if self.ledger is not None:
                entry = PriceBook().get(used_model) or {}
                self.ledger.record(service=self.service, action="chat_turn", provider=entry.get("provider", "anthropic"),
                                   model=entry.get("model", used_model),
                                   usd=usd, client=self.settings.client, input_tokens=usage.input_tokens,
                                   output_tokens=usage.output_tokens, cache_read=usage.cache_read,
                                   cache_write=usage.cache_write, turn_id=turn["id"],
                                   campaign_id=(ctx["campaign_ids"][-1] if ctx["campaign_ids"] else None),
                                   note=text[:120])
            with self.lock:
                turn["reply"] = reply
                turn["model"] = used_model
                turn["usage"] = {"input": usage.input_tokens, "output": usage.output_tokens,
                                 "cache_read": usage.cache_read, "cache_write": usage.cache_write, "usd": usd}
                turn["status"] = "done"
        except Exception as e:  # surfaced to the page, never swallowed
            with self.lock:
                turn["error"] = f"{type(e).__name__}: {e}"
                turn["status"] = "error"
        finally:
            with self.lock:
                turn["finished"] = time.time()
                if self.pending is not None:  # turn died while waiting: release it
                    self.pending["decision"] = False
                    if self._pending_event:
                        self._pending_event.set()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "turns": self.turns,
                "current": self.turn,
                "pending": self.pending,
                "session_usd": round(self.agent.session_usage.usd(self.settings.model), 4)
                if self.agent else 0.0,
            }

    def reset(self) -> None:
        with self.lock:
            if self.turn is not None and self.turn["status"] == "running":
                raise Busy()
            self.turns = []
            self.turn = None
        if self.agent:
            self.agent.reset()


AgentFactory = Callable[[Settings, list], Any]


def _default_agent_factory(settings: Settings, tools: list):
    from .agent import Agent

    return Agent(settings, tools, on_text=lambda _t: None)


def create_app(settings: Settings | None = None, backend: MetaBackend | None = None,
               agent_factory: AgentFactory = _default_agent_factory, ledger: Ledger | None = None) -> Flask:
    settings = settings or load_settings()
    backend = backend or make_backend(settings)
    ledger = ledger or Ledger(settings.ledger_path)
    book = PriceBook()
    session = Session(settings, ledger, service="meta-ads")
    guard = Guard(settings, session.approver, on_event=session.on_log)
    tools = build_tools(backend, guard)
    session.attach(agent_factory(settings, tools))

    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.extensions["session"] = session  # handy for tests
    app.extensions["ledger"] = ledger
    available_models = [m["key"] for m in book.list(kind="text", provider="anthropic")]
    if settings.openai_api_key:
        available_models += [m["key"] for m in book.list(kind="text", provider="openai")]
    app.register_blueprint(make_blueprint(ledger, book, settings.monthly_cap_usd, available_models))

    def cap_reached() -> str | None:
        total = ledger.month_total()
        if total >= settings.monthly_cap_usd:
            return f"Monthly API cap reached: ${total:.2f} of ${settings.monthly_cap_usd:.2f} (MONTHLY_API_CAP_USD). Raise it in .env to continue."
        return None

    def bad(msg: str, code: int = 400):
        return jsonify({"error": msg}), code

    def rows(fn):
        try:
            return jsonify({"rows": fn()})
        except MetaError as e:
            return bad(str(e), 502)

    # -- meta ----------------------------------------------------------------

    @app.get("/api/status")
    def status():
        return jsonify({
            "mode": "dry_run" if settings.dry_run else "live",
            "model": settings.model, "effort": settings.effort,
            "write_accounts": settings.write_accounts,
            "read_accounts": settings.read_accounts,
            "max_daily_budget_minor": settings.max_daily_budget_minor,
            "api_version": settings.meta_api_version or "sdk default",
            "session_usd": session.snapshot()["session_usd"],
            "client": settings.client, "service": "meta-ads",
            "month_usd": ledger.month_total(), "monthly_cap_usd": settings.monthly_cap_usd,
        })

    @app.get("/api/accounts")
    def accounts():
        return rows(lambda: [
            {**a, "writable": a["id"] in settings.write_accounts} for a in backend.list_ad_accounts()
        ])

    @app.get("/api/campaigns")
    def campaigns():
        acct = normalise_account_id(request.args.get("account_id", ""))
        if not acct:
            return bad("account_id required")
        if (why := guard.check_read(acct)):
            return bad(why, 403)
        include = request.args.get("include_archived") == "1"
        return rows(lambda: backend.list_campaigns(acct, include, 200))

    @app.get("/api/adsets")
    def adsets():
        cid = request.args.get("campaign_id", "")
        if not cid:
            return bad("campaign_id required")
        return rows(lambda: backend.list_adsets(cid, 200))

    @app.get("/api/ads")
    def ads():
        aid = request.args.get("adset_id", "")
        if not aid:
            return bad("adset_id required")
        return rows(lambda: backend.list_ads(aid, 200))

    @app.get("/api/insights")
    def insights():
        object_type = request.args.get("object_type", "campaign")
        object_id = request.args.get("object_id", "")
        preset = request.args.get("date_preset", "last_7d")
        if object_type not in OBJECT_TYPES or not object_id:
            return bad("object_type and object_id required")
        if preset not in DATE_PRESETS:
            return bad(f"date_preset must be one of {DATE_PRESETS}")
        if object_type == "account":
            object_id = normalise_account_id(object_id)
        params: dict[str, Any] = {"date_preset": preset, "fields": list(DEFAULT_INSIGHT_FIELDS)}
        if request.args.get("daily") == "1":
            params["time_increment"] = "1"
        return rows(lambda: backend.get_insights(object_type, object_id, params))

    @app.get("/api/log")
    def log():
        return jsonify({"rows": guard.tail(int(request.args.get("n", 15)))})

    # -- chat ----------------------------------------------------------------

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return bad("text required")
        if (why := cap_reached()):
            return bad(why, 402)
        model = body.get("model") or None
        if model and model not in available_models and model not in {m.split("/")[-1] for m in available_models}:
            return bad(f"model {model} is not available in this service")
        try:
            turn = session.start_turn(text, model)
        except Busy:
            return bad("a turn is already running", 409)
        return jsonify({"turn_id": turn["id"]}), 202

    @app.get("/api/turn")
    def turn():
        return jsonify(session.snapshot())

    @app.post("/api/approve")
    def approve():
        body = request.get_json(silent=True) or {}
        ok = session.decide(str(body.get("id", "")), bool(body.get("approve")))
        return jsonify({"ok": ok}) if ok else bad("no such pending approval", 404)

    @app.post("/api/reset")
    def reset():
        try:
            session.reset()
        except Busy:
            return bad("a turn is already running", 409)
        return jsonify({"ok": True})

    return app


def main() -> None:
    import logging

    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no per-request lines
    port = int(os.getenv("META_AGENT_PORT", "4411"))
    app = create_app()
    print(f"meta-ads-agent API on http://127.0.0.1:{port}  "
          f"({'DRY RUN' if app.extensions['session'].settings.dry_run else 'LIVE'})")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
