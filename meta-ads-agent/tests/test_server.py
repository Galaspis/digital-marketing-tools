"""Server state machine: chat turn, blocking approval, decision, reads.
Uses a stand-in agent that calls the real tools against FakeMeta, so the
approval round-trip is exercised end to end without Claude."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from meta_agent.agent import Usage
from meta_agent.config import Settings
from meta_agent.meta_client import FakeMeta
from meta_agent.server import create_app


class StubAgent:
    """Pretends to be Claude: one scripted tool call per message."""

    def __init__(self, settings, tools):
        self.tools = {t.name: t for t in tools}
        self.session_usage = Usage()
        self.on_text = lambda t: None
        self.on_tool_call = None
        self.messages = []

    def send(self, text, model=None):
        self.last_model = model or "claude-opus-5"
        self.on_text("Let me create that.")
        args = {"account_id": FakeMeta.SANDBOX, "name": text, "objective": "OUTCOME_TRAFFIC",
                "daily_budget_minor": 1000, "special_ad_categories": []}
        if self.on_tool_call:
            self.on_tool_call("create_campaign", args)
        result = json.loads(self.tools["create_campaign"].call(args))
        u = Usage(input_tokens=1000, output_tokens=100)
        self.session_usage.input_tokens += 1000
        self.session_usage.output_tokens += 100
        return ("Created " + result["id"]) if "id" in result else ("Failed: " + result["error"]), u

    def reset(self):
        self.messages.clear()


@pytest.fixture
def client(tmp_path: Path):
    s = Settings(None, None, None, "", [], [FakeMeta.SANDBOX], 5000, "claude-opus-5", "high", True,
                 tmp_path / "actions.jsonl", 100.0, "agency", tmp_path / "ledger.sqlite", None)
    app = create_app(s, FakeMeta(), StubAgent)
    app.testing = True
    return app.test_client()


def wait_for(client, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get("/api/turn").get_json()
        if predicate(snap):
            return snap
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_status_and_reads(client):
    st = client.get("/api/status").get_json()
    assert st["mode"] == "dry_run" and st["write_accounts"] == [FakeMeta.SANDBOX]
    accts = client.get("/api/accounts").get_json()["rows"]
    assert {a["id"]: a["writable"] for a in accts} == {FakeMeta.SANDBOX: True, FakeMeta.CLIENT: False}
    camps = client.get(f"/api/campaigns?account_id={FakeMeta.CLIENT}").get_json()["rows"]
    assert len(camps) == 3
    adsets = client.get(f"/api/adsets?campaign_id={camps[0]['id']}").get_json()["rows"]
    assert adsets
    ins = client.get(f"/api/insights?object_type=account&object_id=222&date_preset=last_7d&daily=1").get_json()
    assert len(ins["rows"]) == 7
    assert client.get("/api/insights?object_type=campaign&object_id=x&date_preset=bogus").status_code == 400
    assert client.get("/api/campaigns").status_code == 400


def test_chat_blocks_on_approval_then_completes(client):
    r = client.post("/api/chat", json={"text": "UI test campaign"})
    assert r.status_code == 202

    snap = wait_for(client, lambda s: s["pending"] is not None)
    pending = snap["pending"]
    assert pending["action"] == "create_campaign" and pending["decision"] is None
    assert pending["money"] and "10.00" in pending["money"][0]
    kinds = [e["type"] for e in snap["current"]["events"]]
    assert kinds == ["text", "tool", "approval"]

    # Busy while a turn runs
    assert client.post("/api/chat", json={"text": "another"}).status_code == 409
    assert client.post("/api/reset").status_code == 409

    assert client.post("/api/approve", json={"id": "nope", "approve": True}).status_code == 404
    assert client.post("/api/approve", json={"id": pending["id"], "approve": True}).get_json()["ok"]

    snap = wait_for(client, lambda s: s["current"]["status"] == "done")
    assert snap["current"]["reply"].startswith("Created ")
    assert snap["current"]["usage"]["usd"] > 0 and snap["session_usd"] > 0
    assert snap["current"]["events"][-1]["decision"] is True

    log = client.get("/api/log").get_json()["rows"]
    assert log[-1]["approved"] is True

    assert client.post("/api/reset").get_json()["ok"]
    assert client.get("/api/turn").get_json()["turns"] == []


def test_decline_returns_refusal_to_agent(client):
    client.post("/api/chat", json={"text": "declined one"})
    snap = wait_for(client, lambda s: s["pending"] is not None)
    client.post("/api/approve", json={"id": snap["pending"]["id"], "approve": False})
    snap = wait_for(client, lambda s: s["current"]["status"] == "done")
    assert "Operator declined" in snap["current"]["reply"]
    assert any(e["type"] == "refused" for e in snap["current"]["events"])


def test_ledger_records_turn_and_exposes_models(client):
    models = client.get("/api/models?kind=text").get_json()["rows"]
    assert any(m["key"] == "anthropic/claude-opus-5" and m["available"] for m in models)
    assert any(m["provider"] == "openai" and not m["available"] for m in models)  # listed, not yet runnable here
    assert client.post("/api/chat", json={"text": "x", "model": "openai/gpt-5.6-sol"}).status_code == 400

    client.post("/api/chat", json={"text": "Ledger test", "model": "anthropic/claude-sonnet-5"})
    snap = wait_for(client, lambda s: s["pending"] is not None)
    client.post("/api/approve", json={"id": snap["pending"]["id"], "approve": True})
    snap = wait_for(client, lambda s: s["current"]["status"] == "done")
    assert snap["current"]["model"].endswith("claude-sonnet-5")  # full price-book key

    summary = client.get("/api/ledger/summary").get_json()
    assert summary["total"] > 0 and summary["by_service"][0]["service"] == "meta-ads"
    assert summary["by_model"][0]["model"] == "claude-sonnet-5"
    assert summary["per_campaign"] and summary["per_campaign"][0]["usd"] > 0  # turn cost attached to the created campaign
    assert summary["monthly_cap_usd"] == 100.0 and summary["cap_reached"] is False
    st = client.get("/api/status").get_json()
    assert st["month_usd"] == summary["total"]
