from datetime import datetime, timezone
from pathlib import Path

from agentkit import Ledger, PriceBook, begin_turn, current_turn, note_post

BOOK = Path(__file__).resolve().parents[2] / "pricing.json"


def test_pricebook_text_and_image_costs():
    b = PriceBook(BOOK)
    assert b.get("claude-opus-5")["key"] == "anthropic/claude-opus-5"
    # 1M input + 1M output on Opus 5 = $30 by the published rates
    assert b.text_cost("anthropic/claude-opus-5", 1_000_000, 1_000_000) == 30.0
    assert b.text_cost("openai/gpt-5.6-sol", 500_000, 100_000) == 4.0
    usage = {"input_tokens": 100, "input_tokens_details": {"text_tokens": 100, "image_tokens": 0}, "output_tokens": 1056}
    assert round(b.image_cost("openai/gpt-image-2", usage), 4) == round((100 * 5 + 1056 * 30) / 1e6, 4)
    low_tokens = b.get("openai/gpt-image-2")["typical_output_tokens"]["low"]
    assert b.image_typical_cost("openai/gpt-image-2", "low") == round(low_tokens * 30 / 1e6, 4)
    assert b.text_cost("nope/unknown", 10, 10) == 0.0
    assert {m["provider"] for m in b.list(kind="text")} == {"anthropic", "openai"}


def test_ledger_records_attaches_and_summarises(tmp_path):
    l = Ledger(tmp_path / "ledger.sqlite")
    ts = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    l.record(service="social", action="generate_image", provider="openai", model="gpt-image-2", usd=0.04,
             images=1, ref_type="image", ref_id="generated/a.png", ts=ts)
    l.record(service="social", action="chat_turn", provider="anthropic", model="claude-opus-5", usd=0.06,
             input_tokens=12000, output_tokens=800, turn_id="t1", ts=ts)
    l.record(service="meta-ads", action="chat_turn", provider="anthropic", model="claude-opus-5", usd=0.12,
             turn_id="t2", campaign_id="c9", ts=ts)
    assert l.month_total("2026-09") == 0.22
    assert l.attach_post("p1", ref_ids=["generated/a.png", "https://x/a.png"], turn_id="t1") == 2
    assert l.post_costs(["p1"]) == {"p1": 0.1}
    s = l.summary("2026-09")
    assert s["total"] == 0.22 and s["by_service"][0]["service"] == "meta-ads"
    assert s["per_post"][0] == {**s["per_post"][0], "post_id": "p1", "usd": 0.1}
    assert s["per_campaign"][0]["campaign_id"] == "c9"
    assert s["by_day"][0]["day"] == "2026-09-06"
    assert [r["model"] for r in s["by_model"]] == ["claude-opus-5", "gpt-image-2"]
    assert len(l.rows(post_id="p1")) == 2


def test_turn_context_is_per_thread():
    assert current_turn() is None
    begin_turn("t1", client="agency", service="social")
    note_post("p1"); note_post("p1"); note_post("p2")
    assert current_turn()["post_ids"] == ["p1", "p2"]
