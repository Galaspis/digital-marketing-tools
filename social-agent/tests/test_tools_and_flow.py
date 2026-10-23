"""Drafting, policy, publishing, scheduling and the scheduler, all against
the fake Meta channel. No network, no Claude."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from social_agent.channels.meta import FakeMetaChannel
from social_agent.config import FAKE_IG, FAKE_PAGE, Settings
from social_agent.guard import Guard
from social_agent.images import FakeImageGenerator
from social_agent.media import FakeMediaStore
from social_agent.publisher import Publisher
from social_agent.scheduler import Scheduler
from social_agent.store import Store
from social_agent.tools import build_tools


def settings(tmp_path: Path, **over) -> Settings:
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    base = dict(meta_app_id=None, meta_app_secret=None, meta_user_token=None, graph_version="v26.0",
                publish_targets=[FAKE_PAGE, FAKE_IG], max_posts_per_day=10,
                r2_endpoint=None, r2_access_key=None, r2_secret_key=None, r2_bucket=None, r2_public_base=None,
                media_dir=media_dir, openai_api_key=None, image_model="gpt-image-2", max_images_per_day=20,
                image_price_text_in=5.0, image_price_image_in=10.0, image_price_image_out=40.0,
                model="claude-opus-5", effort="high", fallbacks=True,
                db_path=tmp_path / "posts.sqlite", log_path=tmp_path / "actions.jsonl",
                monthly_cap_usd=100.0, client="agency", ledger_path=tmp_path / "ledger.sqlite")
    base.update(over)
    return Settings(**base)


class Rig:
    def __init__(self, tmp_path, approve=True, **over):
        self.s = settings(tmp_path, **over)
        self.channel = FakeMetaChannel()
        self.store = Store(self.s.db_path)
        self.decisions = []
        self.guard = Guard(self.s, lambda req: self.decisions.append(req) or approve, self.store.published_since)
        self.publisher = Publisher(self.channel, self.store)
        self.media = FakeMediaStore()
        self.tools = {t.name: t for t in build_tools(self.channel, self.store, self.guard, self.media, self.s, self.publisher,
                                                     FakeImageGenerator(self.s))}

    def call(self, name, **kw):
        return json.loads(self.tools[name].call(kw))


def in_hours(h: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat(timespec="seconds")


def test_targets_and_draft_facebook(tmp_path):
    r = Rig(tmp_path)
    targets = r.call("list_targets")
    assert {t["id"] for t in targets} == {FAKE_PAGE, FAKE_IG} and all(t["publish_allowed"] for t in targets)
    d = r.call("create_draft", target_id=FAKE_PAGE, kind="text", text="שלום עולם")
    assert d["status"] == "draft" and d["target_type"] == "facebook_page" and d["text"] == "שלום עולם"
    assert r.call("list_posts", status="draft")[0]["id"] == d["id"]
    assert r.decisions == []  # drafting never asks


def test_instagram_rules(tmp_path):
    r = Rig(tmp_path)
    assert "text-only" in r.call("create_draft", target_id=FAKE_IG, kind="text", text="x")["error"]
    assert "need at least one" in r.call("create_draft", target_id=FAKE_IG, kind="photo", text="x")["error"]
    (r.s.media_dir / "pic.jpg").write_bytes(b"jpg")
    d = r.call("create_draft", target_id=FAKE_IG, kind="photo", text="caption", media=["pic.jpg"])
    assert d["media"][0].startswith("https://media.fake.local/")  # local file was "uploaded"
    assert "Only files under" in r.call("upload_media", file_name="../../etc/passwd")["error"]


def test_publish_requires_approval_and_policy(tmp_path):
    r = Rig(tmp_path, approve=False)
    d = r.call("create_draft", target_id=FAKE_PAGE, kind="text", text="hello")
    out = r.call("publish_post", post_id=d["id"])
    assert out["error"] == "Operator declined this action." and len(r.decisions) == 1
    assert r.store.get(d["id"])["status"] == "draft"
    assert "text: hello" in r.decisions[0].summary[2]

    r2 = Rig(tmp_path / "b", publish_targets=[FAKE_IG])  # Page not allowlisted
    d2 = r2.call("create_draft", target_id=FAKE_PAGE, kind="text", text="hello")
    assert "not enabled" in r2.call("publish_post", post_id=d2["id"])["error"] and r2.decisions == []


def test_publish_and_daily_cap(tmp_path):
    r = Rig(tmp_path, max_posts_per_day=2)
    ids = [r.call("create_draft", target_id=FAKE_PAGE, kind="text", text=f"p{i}")["id"] for i in range(3)]
    for i in range(2):
        out = r.call("publish_post", post_id=ids[i])
        assert out["status"] == "published" and out["permalink"].startswith("https://www.facebook.com/")
    assert "cap is 2" in r.call("publish_post", post_id=ids[2])["error"]
    log = [json.loads(l) for l in r.s.log_path.read_text(encoding="utf-8").splitlines()]
    assert [e["approved"] for e in log if e["action"] == "publish"] == [True, True, False]


def test_schedule_native_vs_local_and_cancel(tmp_path):
    r = Rig(tmp_path)
    fb = r.call("create_draft", target_id=FAKE_PAGE, kind="link", text="see", link="https://example.com")
    assert "10 minutes" in r.call("schedule_post", post_id=fb["id"], scheduled_at=in_hours(0.05))["error"]
    assert "30 days" in r.call("schedule_post", post_id=fb["id"], scheduled_at=in_hours(24 * 31))["error"]
    s = r.call("schedule_post", post_id=fb["id"], scheduled_at=in_hours(2))
    assert s["status"] == "scheduled" and s["scheduler"] == "native" and s["remote_id"] in r.channel.scheduled

    ig = r.call("create_draft", target_id=FAKE_IG, kind="reel", text="🎬", media=["https://cdn.example.com/v.mp4"])
    s2 = r.call("schedule_post", post_id=ig["id"], scheduled_at=in_hours(2))
    assert s2["scheduler"] == "local" and s2["remote_id"] is None

    c = r.call("cancel_post", post_id=fb["id"])
    assert c["status"] == "cancelled" and s["remote_id"] not in r.channel.scheduled
    assert "only drafts" in r.call("update_draft", post_id=ig["id"], text="x")["error"]


def test_scheduler_publishes_due_local_posts(tmp_path):
    r = Rig(tmp_path)
    ig = r.call("create_draft", target_id=FAKE_IG, kind="photo", text="due", media=["https://cdn.example.com/a.jpg"])
    r.store.update(ig["id"], status="scheduled", scheduler="local",
                   scheduled_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"))
    future = r.call("create_draft", target_id=FAKE_IG, kind="photo", text="later", media=["https://cdn.example.com/b.jpg"])
    r.call("schedule_post", post_id=future["id"], scheduled_at=in_hours(3))
    n = Scheduler(r.publisher, r.guard).tick()
    assert n == 1
    assert r.store.get(ig["id"])["status"] == "published"
    assert r.store.get(future["id"])["status"] == "scheduled"


def test_failed_publish_keeps_error_and_is_editable(tmp_path):
    r = Rig(tmp_path)
    d = r.call("create_draft", target_id=FAKE_PAGE, kind="photo", text="no media yet")
    out = r.call("publish_post", post_id=d["id"])
    assert out["status"] == "failed" and "needs media" in out["error"]
    fixed = r.call("update_draft", post_id=d["id"], media=["https://cdn.example.com/a.jpg"])
    assert fixed["status"] == "draft" and fixed["error"] is None
    assert r.call("publish_post", post_id=d["id"])["status"] == "published"


def test_generate_image_then_use_in_draft(tmp_path):
    r = Rig(tmp_path, max_images_per_day=2)
    img = r.call("generate_image", prompt="Freight containers at Haifa port at sunrise, clean editorial photo", size="1536x1024")
    assert img["file"].startswith("generated/") and img["file"].endswith(".png")
    assert (r.s.media_dir / img["file"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert img["estimated_cost_usd"] > 0 and img["model"] == "fake-image"
    assert "size must be" in r.call("generate_image", prompt="x", size="800x600")["error"]

    d = r.call("create_draft", target_id=FAKE_IG, kind="photo", text="Haifa at dawn", media=[img["file"]])
    assert d["media"][0].startswith("https://media.fake.local/") and r.media.local_for(d["media"][0]).name == img["file"].split("/")[-1]

    r.call("generate_image", prompt="second")
    assert "Daily image cap" in r.call("generate_image", prompt="third")["error"]
    log = [json.loads(l) for l in r.s.log_path.read_text(encoding="utf-8").splitlines() if "generate_image" in l]
    assert len(log) == 2 and log[0]["details"]["cost_usd"] > 0
