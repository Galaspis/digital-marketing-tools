"""Policy and tool tests. No network, no Claude, no Meta."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meta_agent.config import Settings
from meta_agent.guard import Guard
from meta_agent.meta_client import FakeMeta
from meta_agent.tools import build_tools


def settings(tmp_path: Path, **over) -> Settings:
    base = dict(
        meta_app_id=None, meta_app_secret=None, meta_access_token=None, meta_api_version="v25.0",
        read_accounts=[], write_accounts=[FakeMeta.SANDBOX], max_daily_budget_minor=5000,
        model="claude-opus-5", effort="high", fallbacks=True, log_path=tmp_path / "actions.jsonl",
        monthly_cap_usd=100.0, client="agency", ledger_path=tmp_path / "ledger.sqlite", openai_api_key=None,
    )
    base.update(over)
    return Settings(**base)


def make(tmp_path, approve=True, **over):
    s = settings(tmp_path, **over)
    backend = FakeMeta()
    decisions = []

    def approver(prompt):
        decisions.append(prompt)
        return approve

    guard = Guard(s, approver)
    tools = {t.name: t for t in build_tools(backend, guard)}
    return backend, guard, tools, decisions, s


def call(tool, **kwargs):
    return json.loads(tool.call(kwargs))


def test_tool_schemas_are_generated():
    backend = FakeMeta()
    tools = build_tools(backend, Guard(settings(Path(".")), lambda _: False, Path("logs/test.jsonl")))
    names = {t.name for t in tools}
    assert {"list_campaigns", "get_insights", "create_campaign", "set_status", "set_budget"} <= names
    schema = next(t for t in tools if t.name == "get_insights").to_dict()
    assert schema["input_schema"]["properties"]["object_type"]["type"] == "string"
    assert "date_preset" in schema["input_schema"]["properties"]


def test_reads_work_on_any_account(tmp_path):
    _, _, tools, _, _ = make(tmp_path)
    accounts = call(tools["list_ad_accounts"])
    assert {a["id"] for a in accounts} == {FakeMeta.SANDBOX, FakeMeta.CLIENT}
    camps = call(tools["list_campaigns"], account_id="222")  # digits only -> act_222
    assert len(camps) == 3 and all(c["effective_status"] in ("ACTIVE", "PAUSED") for c in camps)
    adsets = call(tools["list_adsets"], campaign_id=camps[0]["id"])
    assert adsets and adsets[0]["campaign_id"] == camps[0]["id"]
    rows = call(tools["get_insights"], object_type="campaign", object_id=camps[0]["id"], date_preset="last_7d")
    assert len(rows) == 1 and float(rows[0]["spend"]) > 0
    daily = call(tools["get_insights"], object_type="account", object_id="act_222", daily=True)
    assert len(daily) == 7


def test_read_allowlist_blocks_other_accounts(tmp_path):
    _, _, tools, _, _ = make(tmp_path, read_accounts=[FakeMeta.SANDBOX])
    out = call(tools["list_campaigns"], account_id=FakeMeta.CLIENT)
    assert "not allowed" in out["error"]


def test_write_on_client_account_refused_without_prompt(tmp_path):
    _, _, tools, decisions, s = make(tmp_path)
    out = call(tools["create_campaign"], account_id=FakeMeta.CLIENT, name="x",
               objective="OUTCOME_LEADS", daily_budget_minor=1000)
    assert "read-only" in out["error"]
    assert decisions == []  # never even asked
    entry = json.loads(s.log_path.read_text().splitlines()[-1])
    assert entry["approved"] is False and entry["action"] == "create_campaign"


def test_write_over_budget_cap_refused(tmp_path):
    _, _, tools, decisions, _ = make(tmp_path)
    out = call(tools["create_campaign"], account_id=FakeMeta.SANDBOX, name="x",
               objective="OUTCOME_LEADS", daily_budget_minor=5001)
    assert "exceeds the cap" in out["error"] and decisions == []


def test_write_declined_by_operator(tmp_path):
    backend, _, tools, decisions, _ = make(tmp_path, approve=False)
    before = len(backend.campaigns)
    out = call(tools["create_campaign"], account_id=FakeMeta.SANDBOX, name="x",
               objective="OUTCOME_LEADS", daily_budget_minor=1000)
    assert out["error"] == "Operator declined this action."
    assert len(decisions) == 1 and len(backend.campaigns) == before


def test_full_sandbox_flow_create_pause_budget(tmp_path):
    backend, _, tools, decisions, s = make(tmp_path)
    camp = call(tools["create_campaign"], account_id=FakeMeta.SANDBOX, name="Test",
                objective="OUTCOME_TRAFFIC", daily_budget_minor=2000, special_ad_categories=[])
    assert camp["status"] == "PAUSED" and camp["daily_budget"] == "2000"

    adset = call(tools["create_adset"], account_id=FakeMeta.SANDBOX, campaign_id=camp["id"],
                 name="IL 25-45", daily_budget_minor=1500, optimization_goal="LINK_CLICKS",
                 billing_event="IMPRESSIONS", countries=["IL"], age_min=25, age_max=45)
    assert adset["targeting"]["geo_locations"]["countries"] == ["IL"] and adset["status"] == "PAUSED"

    on = call(tools["set_status"], object_type="campaign", object_id=camp["id"], status="ACTIVE")
    assert on["updated"] == {"status": "ACTIVE"}
    assert backend.campaigns[camp["id"]]["effective_status"] == "ACTIVE"

    off = call(tools["set_status"], object_type="campaign", object_id=camp["id"], status="PAUSED")
    assert off["updated"] == {"status": "PAUSED"}

    b = call(tools["set_budget"], object_type="adset", object_id=adset["id"], daily_budget_minor=1800)
    assert b["updated"] == {"daily_budget": 1800}
    assert backend.adsets[adset["id"]]["daily_budget"] == "1800"

    assert len(decisions) == 5  # one approval per write
    approved = [json.loads(l) for l in s.log_path.read_text().splitlines() if json.loads(l)["approved"]]
    assert len(approved) == 5


def test_sandbox_insights_are_zero(tmp_path):
    _, _, tools, _, _ = make(tmp_path)
    rows = call(tools["get_insights"], object_type="account", object_id=FakeMeta.SANDBOX)
    assert rows[0]["spend"] == "0.00" and rows[0]["impressions"] == "0"


def test_bad_inputs_return_errors_not_exceptions(tmp_path):
    _, _, tools, _, _ = make(tmp_path)
    assert "objective" in call(tools["create_campaign"], account_id=FakeMeta.SANDBOX, name="x",
                               objective="TRAFFIC", daily_budget_minor=100)["error"]
    assert "exactly one" in call(tools["create_campaign"], account_id=FakeMeta.SANDBOX, name="x",
                                 objective="OUTCOME_TRAFFIC")["error"]
    assert "does not exist" in call(tools["list_adsets"], campaign_id="nope")["error"]
    assert "date_preset" in call(tools["get_insights"], object_type="account",
                                 object_id="act_222", date_preset="last_year")["error"]
