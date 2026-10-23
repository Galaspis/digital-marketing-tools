"""Local HTTP API for the hub page. `npm start` in the hub launches it
(`serve.py`), and the hub proxies /api/social/* here. Binds to 127.0.0.1.

Chat turns run in a background thread and block on approval cards exactly
like the ads agent. Post CRUD, publish/schedule/cancel and media upload are
also exposed directly for the calendar UI; a click there counts as the
operator's approval (still allowlist + cap checked, still logged)."""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, redirect, request, send_file

from agentkit import Ledger, PriceBook, begin_turn
from agentkit.ledger_api import make_blueprint

from .channels.base import ChannelError
from .channels.meta import make_channel
from .config import Settings, load_settings
from .guard import ApprovalRequest, Guard
from .images import QUALITIES, SIZES, ImageError, images_in_last_day, make_image_generator
from .media import MediaError, confine, make_media_store, resolve_media
from .publisher import Publisher, PublishError
from .scheduler import Scheduler
from .store import POST_KINDS, STATUSES, Store, to_utc_iso
from .tools import _summary, build_tools

APPROVAL_TIMEOUT_SECONDS = 600


def _media_keys(media_store, items):
    """For hosted URLs whose local file we know, also return the media-folder
    paths the ledger may have recorded them under, so image rows attach to the post."""
    out = []
    local_for = getattr(media_store, "local_for", None)
    for item in items or []:
        p = local_for(item) if local_for else None
        if p is not None:
            out += ["generated/" + p.name, "uploads/" + p.name, p.as_posix()]
    return out


class Busy(Exception):
    pass


class Session:
    def __init__(self, settings: Settings, ledger: Ledger | None = None, service: str = "social") -> None:
        self.settings = settings
        self.ledger = ledger
        self.service = service
        self.lock = threading.Lock()
        self.turns: list[dict[str, Any]] = []
        self.turn: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None
        self._pending_event: threading.Event | None = None
        self.agent = None

    def attach(self, agent) -> None:
        self.agent = agent
        agent.on_text = lambda text: self._event({"type": "text", "text": text})
        agent.on_tool_call = lambda name, inp: self._event({"type": "tool", "name": name, "input": inp})

    def _event(self, event: dict[str, Any]) -> None:
        with self.lock:
            if self.turn is not None and self.turn["status"] == "running":
                event.setdefault("ts", time.time())
                self.turn["events"].append(event)

    def on_log(self, entry: dict[str, Any]) -> None:
        if entry.get("approved") is False and entry.get("note"):
            self._event({"type": "refused", "action": entry["action"], "note": entry["note"]})

    def approver(self, req: ApprovalRequest) -> bool:
        pending = {"type": "approval", "id": uuid.uuid4().hex[:10], "action": req.action,
                   "account": req.target_id, "details": req.details, "text": req.text,
                   "money": req.summary, "decision": None, "ts": time.time()}
        event = threading.Event()
        with self.lock:
            self.pending, self._pending_event = pending, event
            if self.turn is not None:
                self.turn["events"].append(pending)
        answered = event.wait(APPROVAL_TIMEOUT_SECONDS)
        with self.lock:
            if not answered:
                pending["decision"] = False
                pending["timed_out"] = True
            self.pending = self._pending_event = None
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

    def start_turn(self, text: str, model: str | None = None) -> dict[str, Any]:
        with self.lock:
            if self.turn is not None and self.turn["status"] == "running":
                raise Busy()
            turn = {"id": uuid.uuid4().hex[:10], "text": text, "status": "running", "events": [],
                    "reply": None, "usage": None, "error": None, "started": time.time(), "finished": None,
                    "model": model or self.settings.model}
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
                post_id = ctx["post_ids"][-1] if ctx["post_ids"] else None
                entry = PriceBook().get(used_model) or {}
                self.ledger.record(service=self.service, action="chat_turn", provider=entry.get("provider", "anthropic"),
                                   model=entry.get("model", used_model),
                                   usd=usd, client=self.settings.client, input_tokens=usage.input_tokens,
                                   output_tokens=usage.output_tokens, cache_read=usage.cache_read,
                                   cache_write=usage.cache_write, turn_id=turn["id"], post_id=post_id,
                                   note=text[:120])
            with self.lock:
                turn.update(reply=reply, status="done", model=used_model, usage={
                    "input": usage.input_tokens, "output": usage.output_tokens,
                    "cache_read": usage.cache_read, "cache_write": usage.cache_write, "usd": usd})
        except Exception as e:
            with self.lock:
                turn.update(error=f"{type(e).__name__}: {e}", status="error")
        finally:
            with self.lock:
                turn["finished"] = time.time()
                if self.pending is not None:
                    self.pending["decision"] = False
                    if self._pending_event:
                        self._pending_event.set()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"turns": self.turns, "current": self.turn, "pending": self.pending,
                    "session_usd": round(self.agent.session_usage.usd(self.settings.model), 4) if self.agent else 0.0}

    def reset(self) -> None:
        with self.lock:
            if self.turn is not None and self.turn["status"] == "running":
                raise Busy()
            self.turns, self.turn = [], None
        if self.agent:
            self.agent.reset()


AgentFactory = Callable[[Settings, list], Any]


def _default_agent_factory(settings: Settings, tools: list):
    from .agent import Agent

    return Agent(settings, tools, on_text=lambda _t: None)


def create_app(settings: Settings | None = None, channel=None, agent_factory: AgentFactory = _default_agent_factory,
               store: Store | None = None, media_store=None, start_scheduler: bool = True,
               image_generator=None, ledger: Ledger | None = None) -> Flask:
    settings = settings or load_settings()
    channel = channel or make_channel(settings)
    store = store or Store(settings.db_path)
    media_store = media_store or make_media_store(settings)
    image_generator = image_generator or make_image_generator(settings)
    ledger = ledger or Ledger(settings.ledger_path)
    book = PriceBook()
    session = Session(settings, ledger, service="social")
    guard = Guard(settings, session.approver, store.published_since, on_event=session.on_log)
    publisher = Publisher(channel, store)
    tools = build_tools(channel, store, guard, media_store, settings, publisher, image_generator, ledger)
    session.attach(agent_factory(settings, tools))
    scheduler = Scheduler(publisher, guard)
    if start_scheduler:
        scheduler.start()

    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.extensions.update(session=session, store=store, scheduler=scheduler, publisher=publisher, guard=guard, ledger=ledger)
    # Only Claude models run in this service's loop today; the OpenAI loop is a later storyboard row.
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

    def guarded(fn):
        try:
            return jsonify(fn())
        except (ChannelError, PublishError, MediaError, ImageError) as e:
            return bad(str(e), 422)
        except KeyError as e:
            return bad(str(e), 404)

    # -- meta ------------------------------------------------------------------

    @app.get("/api/status")
    def status():
        return jsonify({
            "mode": "dry_run" if settings.dry_run else "live", "model": settings.model, "effort": settings.effort,
            "publish_targets": settings.publish_targets, "max_posts_per_day": settings.max_posts_per_day,
            "media_configured": settings.media_configured or settings.dry_run,
            "images_enabled": settings.images_enabled, "image_model": settings.image_model,
            "openai_key": bool(settings.openai_api_key),
            "image_sizes": SIZES, "image_qualities": QUALITIES,
            "images_today": images_in_last_day(guard), "max_images_per_day": settings.max_images_per_day,
            "media_dir": str(settings.media_dir), "session_usd": session.snapshot()["session_usd"],
            "kinds": POST_KINDS, "client": settings.client, "service": "social",
            "month_usd": ledger.month_total(), "monthly_cap_usd": settings.monthly_cap_usd,
        })

    @app.get("/api/targets")
    def targets():
        allowed = set(settings.publish_targets)
        return guarded(lambda: {"rows": [{**t.public(), "publish_allowed": t.id in allowed} for t in channel.list_targets()]})

    # -- posts -----------------------------------------------------------------

    @app.get("/api/posts")
    def posts():
        st = request.args.get("status") or None
        if st and st not in STATUSES:
            return bad(f"status must be one of {STATUSES}")
        rows = store.list(st, request.args.get("target_id") or None, 300)
        costs = ledger.post_costs([r["id"] for r in rows])
        for r in rows:
            r["cost_usd"] = costs.get(r["id"], 0.0)
        return jsonify({"rows": rows})

    @app.post("/api/posts")
    def create_post():
        b = request.get_json(silent=True) or {}
        def go():
            target = channel.target(str(b.get("target_id", "")))
            kind = b.get("kind", "text")
            if kind not in POST_KINDS:
                raise PublishError(f"kind must be one of {POST_KINDS}")
            if target.type == "instagram" and (kind in ("text", "link") or not b.get("media")):
                raise PublishError("Instagram posts need media (photo, reel, story or carousel).")
            media = resolve_media(settings, media_store, b.get("media") or [], need_urls=(target.type == "instagram"))
            post = store.create(target_id=target.id, target_name=target.name, target_type=target.type,
                                channel=channel.name, kind=kind, text=b.get("text") or "", link=b.get("link") or None,
                                media=media, scheduled_at=to_utc_iso(b["scheduled_at"]) if b.get("scheduled_at") else None,
                                source="ui")
            guard.log("create_draft", target.id, {"post_id": post["id"], "kind": kind}, approved=None, note="ui")
            ledger.attach_post(post["id"], ref_ids=[*(b.get("media") or []), *media, *_media_keys(media_store, media)])
            return post
        return guarded(go)

    @app.patch("/api/posts/<post_id>")
    def edit_post(post_id):
        b = request.get_json(silent=True) or {}
        def go():
            post = store.get(post_id)
            if post is None:
                raise KeyError(f"post {post_id} not found")
            if post["status"] not in ("draft", "failed"):
                raise PublishError(f"Post is {post['status']}; cancel it before editing.")
            fields: dict[str, Any] = {"status": "draft", "error": None}
            for k in ("text", "link", "kind"):
                if k in b:
                    fields[k] = b[k]
            if "media" in b:
                fields["media"] = resolve_media(settings, media_store, b["media"] or [],
                                                need_urls=(post["target_type"] == "instagram"))
                ledger.attach_post(post_id, ref_ids=[*(b["media"] or []), *fields["media"], *_media_keys(media_store, fields["media"])])
            if "scheduled_at" in b:
                fields["scheduled_at"] = to_utc_iso(b["scheduled_at"]) if b["scheduled_at"] else None
            return store.update(post_id, **fields)
        return guarded(go)

    @app.delete("/api/posts/<post_id>")
    def delete_post(post_id):
        def go():
            post = store.get(post_id)
            if post is None:
                raise KeyError(f"post {post_id} not found")
            if post["status"] not in ("draft", "failed", "cancelled"):
                raise PublishError(f"Post is {post['status']}; cancel it first.")
            store.delete(post_id)
            guard.log("delete_draft", post["target_id"], {"post_id": post_id}, approved=None, note="ui")
            return {"deleted": post_id}
        return guarded(go)

    def _operator_action(post_id: str, action: str, fn):
        post = store.get(post_id)
        if post is None:
            raise KeyError(f"post {post_id} not found")
        details = {"post_id": post_id, "kind": post["kind"], "target": post["target_name"]}
        if (why := guard.authorize(action, post["target_id"], details, _summary(post), operator_initiated=True)):
            raise PublishError(why)
        return fn(post)

    @app.post("/api/posts/<post_id>/publish")
    def publish_post(post_id):
        return guarded(lambda: _operator_action(post_id, "publish", lambda p: publisher.publish_now(post_id, source="ui")))

    @app.post("/api/posts/<post_id>/schedule")
    def schedule_post(post_id):
        when = (request.get_json(silent=True) or {}).get("scheduled_at")
        return guarded(lambda: _operator_action(post_id, "schedule", lambda p: publisher.schedule(post_id, when)))

    @app.post("/api/posts/<post_id>/cancel")
    def cancel_post(post_id):
        return guarded(lambda: _operator_action(post_id, "cancel", lambda p: publisher.cancel(post_id)))

    # -- media -----------------------------------------------------------------

    @app.post("/api/media")
    def upload():
        f = request.files.get("file")
        if f is None or not f.filename:
            return bad("multipart field 'file' required")
        safe = Path(f.filename).name
        dest_dir = settings.media_dir / "uploads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{uuid.uuid4().hex[:8]}_{safe}"
        f.save(dest)
        rel = dest.relative_to(settings.media_dir).as_posix()
        want_url = request.form.get("host", "1") != "0"
        def go():
            url = media_store.upload(dest) if want_url else None
            guard.log("upload_media", None, {"file": safe, "url": url}, approved=None, note="ui")
            return {"path": rel, "url": url}
        return guarded(go)

    @app.post("/api/media/generate")
    def generate():
        b = request.get_json(silent=True) or {}
        prompt = (b.get("prompt") or "").strip()
        if not prompt:
            return bad("prompt required")
        def go():
            if (why := cap_reached()):
                raise ImageError(why)
            n = images_in_last_day(guard)
            if n >= settings.max_images_per_day:
                raise ImageError(f"Daily image cap reached ({n}/{settings.max_images_per_day}).")
            result = image_generator.generate(prompt, b.get("size") or "1024x1024", b.get("quality") or "medium")
            rel = result["path"].relative_to(settings.media_dir).as_posix()
            u = result.get("usage") or {}
            usd = book.image_cost(f"openai/{settings.image_model}", u) or (result.get("cost_usd") or 0)
            result["cost_usd"] = usd
            guard.log("generate_image", None, {"prompt": prompt[:200], "size": result["size"], "quality": result["quality"],
                                                "model": result["model"], "file": rel, "usage": u,
                                                "cost_usd": usd}, approved=None, note="ui")
            ledger.record(service="social", action="generate_image", provider="openai", model=settings.image_model,
                          usd=usd, client=settings.client, input_tokens=u.get("input_tokens", 0),
                          output_tokens=u.get("output_tokens", 0), images=1, ref_type="image", ref_id=rel,
                          note=f"{result['size']} {result['quality']} · {prompt[:80]}")
            want_url = b.get("host", True)
            url = media_store.upload(result["path"]) if want_url else None
            return {"path": rel, "url": url, "model": result["model"], "estimated_cost_usd": result.get("cost_usd"),
                    "revised_prompt": result.get("revised_prompt")}
        return guarded(go)

    @app.get("/api/media/preview")
    def preview():
        """Serve a media item for thumbnails: local file (path under media_dir),
        a fake-hosting URL we remember, or a redirect to a real public URL."""
        u = request.args.get("u", "")
        if not u:
            return bad("u required")
        local = getattr(media_store, "local_for", lambda _u: None)(u)
        if local is None and not u.startswith(("http://", "https://")):
            try:
                local = confine(settings, u)
            except MediaError as e:
                return bad(str(e), 404)
        if local is not None:
            return send_file(local, max_age=3600)
        return redirect(u)

    @app.get("/api/log")
    def log():
        return jsonify({"rows": guard.tail(int(request.args.get("n", 15)))})

    # -- chat ------------------------------------------------------------------

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
        b = request.get_json(silent=True) or {}
        ok = session.decide(str(b.get("id", "")), bool(b.get("approve")))
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

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    port = int(os.getenv("SOCIAL_AGENT_PORT", "4412"))
    app = create_app()
    s = app.extensions["session"].settings
    print(f"social-agent API on http://127.0.0.1:{port}  ({'DRY RUN' if s.dry_run else 'LIVE'})")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
