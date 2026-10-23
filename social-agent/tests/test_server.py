"""API: UI-driven post lifecycle, media upload, and the chat approval
round-trip with a stub agent."""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from social_agent.agent import Usage
from social_agent.channels.meta import FakeMetaChannel
from social_agent.config import FAKE_IG, FAKE_PAGE
from social_agent.media import FakeMediaStore
from social_agent.server import create_app
from tests.test_tools_and_flow import settings


class StubAgent:
    def __init__(self, s, tools):
        self.tools = {t.name: t for t in tools}
        self.session_usage = Usage()
        self.on_text = lambda t: None
        self.on_tool_call = None

    def send(self, text, model=None):
        self.last_model = model or "claude-opus-5"
        draft = json.loads(self.tools["create_draft"].call({"target_id": FAKE_PAGE, "kind": "text", "text": text}))
        self.on_text("Draft saved, publishing…")
        if self.on_tool_call:
            self.on_tool_call("publish_post", {"post_id": draft["id"]})
        out = json.loads(self.tools["publish_post"].call({"post_id": draft["id"]}))
        self.session_usage.input_tokens += 500
        return (f"Published {out['permalink']}" if "permalink" in out and out.get("status") == "published"
                else f"Not published: {out.get('error')}"), Usage(500, 50)

    def reset(self):
        pass


@pytest.fixture
def client(tmp_path):
    app = create_app(settings(tmp_path), FakeMetaChannel(), StubAgent, media_store=FakeMediaStore(), start_scheduler=False)
    app.testing = True
    return app.test_client()


def wait_for(client, pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        snap = client.get("/api/turn").get_json()
        if pred(snap):
            return snap
        time.sleep(0.02)
    raise AssertionError("timeout")


def test_ui_post_lifecycle(client):
    st = client.get("/api/status").get_json()
    assert st["mode"] == "dry_run" and st["media_configured"] is True
    assert {t["id"] for t in client.get("/api/targets").get_json()["rows"]} == {FAKE_PAGE, FAKE_IG}

    r = client.post("/api/posts", json={"target_id": FAKE_PAGE, "kind": "text", "text": "Hi"})
    post = r.get_json()
    assert r.status_code == 200 and post["status"] == "draft" and post["source"] == "ui"

    assert client.post("/api/posts", json={"target_id": FAKE_IG, "kind": "photo", "text": "x"}).status_code == 422

    edited = client.patch(f"/api/posts/{post['id']}", json={"text": "Hi there", "link": "https://example.com", "kind": "link"}).get_json()
    assert edited["text"] == "Hi there" and edited["kind"] == "link"

    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
    sched = client.post(f"/api/posts/{post['id']}/schedule", json={"scheduled_at": when}).get_json()
    assert sched["status"] == "scheduled" and sched["scheduler"] == "native"
    assert client.patch(f"/api/posts/{post['id']}", json={"text": "nope"}).status_code == 422

    assert client.post(f"/api/posts/{post['id']}/cancel").get_json()["status"] == "cancelled"
    assert client.delete(f"/api/posts/{post['id']}").get_json() == {"deleted": post["id"]}
    assert client.get("/api/posts").get_json()["rows"] == []

    p2 = client.post("/api/posts", json={"target_id": FAKE_PAGE, "kind": "text", "text": "now"}).get_json()
    pub = client.post(f"/api/posts/{p2['id']}/publish").get_json()
    assert pub["status"] == "published" and pub["source"] == "ui"
    log = client.get("/api/log").get_json()["rows"]
    assert log[-1]["action"] == "publish" and log[-1]["approved"] is True and "UI" in log[-1]["note"]


def test_media_upload(client):
    data = {"file": (io.BytesIO(b"\xff\xd8jpg"), "photo.jpg")}
    r = client.post("/api/media", data=data, content_type="multipart/form-data")
    body = r.get_json()
    assert r.status_code == 200 and body["url"].startswith("https://media.fake.local/") and body["path"].startswith("uploads")
    assert client.post("/api/media", data={}, content_type="multipart/form-data").status_code == 400


def test_chat_approval_round_trip(client):
    assert client.post("/api/chat", json={"text": "Post: big news"}).status_code == 202
    snap = wait_for(client, lambda s: s["pending"] is not None)
    p = snap["pending"]
    assert p["action"] == "publish" and any("text: Post: big news" in m for m in p["money"])
    assert client.post("/api/approve", json={"id": p["id"], "approve": True}).get_json()["ok"]
    snap = wait_for(client, lambda s: s["current"]["status"] == "done")
    assert snap["current"]["reply"].startswith("Published https://www.facebook.com/")
    assert snap["current"]["events"][-1]["decision"] is True

    client.post("/api/chat", json={"text": "another"})
    snap = wait_for(client, lambda s: s["pending"] is not None)
    client.post("/api/approve", json={"id": snap["pending"]["id"], "approve": False})
    snap = wait_for(client, lambda s: s["current"]["status"] == "done")
    assert "Operator declined" in snap["current"]["reply"]
    assert client.get("/api/posts?status=draft").get_json()["rows"][0]["text"] == "another"


def test_generate_and_preview_endpoints(client):
    r = client.post("/api/media/generate", json={"prompt": "blue square", "size": "1024x1024", "quality": "low"})
    body = r.get_json()
    assert r.status_code == 200 and body["path"].startswith("generated/") and body["url"].startswith("https://media.fake.local/")
    assert body["estimated_cost_usd"] > 0
    pv = client.get("/api/media/preview?u=" + body["url"])
    assert pv.status_code == 200 and pv.data[:8] == b"\x89PNG\r\n\x1a\n"
    pv2 = client.get("/api/media/preview?u=" + body["path"])
    assert pv2.status_code == 200
    assert client.get("/api/media/preview?u=../../secret.png").status_code == 404
    assert client.get("/api/media/preview?u=https://cdn.example.com/x.jpg").status_code == 302
    st = client.get("/api/status").get_json()
    assert st["images_enabled"] is True and st["images_today"] == 1
    assert client.post("/api/media/generate", json={"prompt": ""}).status_code == 400


def test_costs_attach_to_posts_and_cap_blocks(client):
    img = client.post("/api/media/generate", json={"prompt": "sunset", "quality": "low"}).get_json()
    post = client.post("/api/posts", json={"target_id": FAKE_IG, "kind": "photo", "text": "hi", "media": [img["url"]]}).get_json()
    rows = client.get("/api/posts").get_json()["rows"]
    mine = next(r for r in rows if r["id"] == post["id"])
    assert mine["cost_usd"] > 0  # the image's cost now belongs to this post

    client.post("/api/chat", json={"text": "Turn attached", "model": "anthropic/claude-haiku-4-5"})
    snap = wait_for(client, lambda s: s["pending"] is not None)
    client.post("/api/approve", json={"id": snap["pending"]["id"], "approve": True})
    wait_for(client, lambda s: s["current"]["status"] == "done")
    summary = client.get("/api/ledger/summary").get_json()
    assert {r["action"] for r in summary["by_action"]} == {"generate_image", "chat_turn"}
    assert summary["per_post"] and all(r["usd"] > 0 for r in summary["per_post"])
    assert any(r["model"] == "claude-haiku-4-5" for r in summary["by_model"])

    # a tiny cap makes every paid action refuse with a clear message
    ledger = client.application.extensions["ledger"]
    ledger.record(service="social", action="chat_turn", provider="anthropic", model="claude-opus-5", usd=1000.0)
    r = client.post("/api/chat", json={"text": "over"})
    assert r.status_code == 402 and "Monthly API cap reached" in r.get_json()["error"]
    r = client.post("/api/media/generate", json={"prompt": "over"})
    assert r.status_code == 422 and "cap reached" in r.get_json()["error"].lower()
    assert client.get("/api/ledger/summary").get_json()["cap_reached"] is True
