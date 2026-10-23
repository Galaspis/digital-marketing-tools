"""The chosen model decides the provider. OpenAI turns run through the shared
OpenAI loop (faked here), are priced from the price book and land in the
ledger as provider=openai. No network."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace as NS

from social_agent.agent import Agent
from social_agent.channels.meta import FakeMetaChannel
from social_agent.config import FAKE_PAGE
from social_agent.guard import Guard
from social_agent.media import FakeMediaStore
from social_agent.publisher import Publisher
from social_agent.server import create_app
from social_agent.store import Store
from social_agent.tools import build_tools
from tests.test_tools_and_flow import settings


class ScriptedOpenAI:
    """First call: ask for create_draft. Second call: final text."""

    def __init__(self):
        self.calls = []

    @property
    def responses(self):
        return self

    def create(self, **kw):
        self.calls.append(kw)
        usage = NS(input_tokens=1000, output_tokens=50, input_tokens_details=NS(cached_tokens=200))
        if len(self.calls) == 1:
            args = json.dumps({"target_id": FAKE_PAGE, "kind": "text", "text": "Hello from GPT"})
            return NS(id="r1", usage=usage, output=[NS(type="function_call", call_id="c1", name="create_draft", arguments=args)])
        return NS(id=f"r{len(self.calls)}", usage=usage, output=[NS(type="message", content=[NS(type="output_text", text="Draft saved.")])])


def make_agent(tmp_path):
    s = settings(tmp_path, openai_api_key="sk-test")
    store = Store(s.db_path)
    guard = Guard(s, lambda req: True, store.published_since)
    tools = build_tools(FakeMetaChannel(), store, guard, FakeMediaStore(), s, Publisher(FakeMetaChannel(), store))
    agent = Agent(s, tools, on_text=lambda t: None, client=None)
    agent._oa_client = ScriptedOpenAI()
    return agent, store


def test_resolve_and_openai_turn(tmp_path):
    agent, store = make_agent(tmp_path)
    assert agent._resolve("openai/gpt-5.6-sol") == ("openai/gpt-5.6-sol", "openai", "gpt-5.6-sol")
    assert agent._resolve("claude-sonnet-5") == ("anthropic/claude-sonnet-5", "anthropic", "claude-sonnet-5")
    assert agent._resolve(None)[1] == "anthropic"

    text, usage = agent.send("write a hello post", model="openai/gpt-5.6-sol")
    assert text == "Draft saved." and agent.last_model == "openai/gpt-5.6-sol"
    assert store.list("draft")[0]["text"] == "Hello from GPT"          # the tool really ran
    assert usage.input_tokens == 1600 and usage.cache_read == 400 and usage.output_tokens == 100
    # priced from the book: 1600*4 + 400*0.4 + 100*20 per 1M
    assert round(usage.usd("openai/gpt-5.6-sol"), 6) == round((1600 * 4 + 400 * 0.4 + 100 * 20) / 1e6, 6)
    calls = agent._oa_client.calls
    assert calls[0]["model"] == "gpt-5.6-sol" and calls[0]["tools"][0]["type"] == "function"
    assert calls[1]["previous_response_id"] == "r1"
    assert agent.transcript == [("user", "write a hello post"), ("assistant", "Draft saved.")]
    agent.reset()
    assert agent.transcript == [] and agent._oa_loop.previous_response_id is None


def test_openai_without_key_is_refused(tmp_path):
    agent, _ = make_agent(tmp_path)
    object.__setattr__(agent, "settings", settings(tmp_path / "nokey"))
    try:
        agent.send("x", model="openai/gpt-5.6-sol")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "OPENAI_API_KEY" in str(e)


class StubAgent:
    def __init__(self, s, tools):
        self.session_usage = NS(usd=lambda m: 0.0)
        self.on_text = lambda t: None
        self.on_tool_call = None
        self.last_model = ""
    def send(self, text, model=None):
        self.last_model = model or "anthropic/claude-opus-5"
        u = NS(input_tokens=100, output_tokens=10, cache_read=0, cache_write=0, usd=lambda m: 0.001)
        return f"ran on {model}", u
    def reset(self):
        pass


def test_server_offers_openai_models_when_key_set(tmp_path):
    app = create_app(settings(tmp_path, openai_api_key="sk-test"), FakeMetaChannel(), StubAgent,
                     media_store=FakeMediaStore(), start_scheduler=False)
    app.testing = True
    c = app.test_client()
    models = {m["key"]: m["available"] for m in c.get("/api/models?kind=text").get_json()["rows"]}
    assert models["openai/gpt-5.6-sol"] is True and models["anthropic/claude-opus-5"] is True
    assert c.post("/api/chat", json={"text": "hi", "model": "openai/gpt-5.6-sol"}).status_code == 202
    for _ in range(100):
        snap = c.get("/api/turn").get_json()
        if snap["current"]["status"] == "done":
            break
        time.sleep(0.02)
    assert snap["current"]["reply"] == "ran on openai/gpt-5.6-sol" and snap["current"]["model"] == "openai/gpt-5.6-sol"
    row = c.get("/api/ledger/rows?limit=1").get_json()["rows"][0]
    assert row["provider"] == "openai" and row["model"] == "gpt-5.6-sol"
    assert c.post("/api/chat", json={"text": "hi", "model": "openai/not-a-model"}).status_code == 400
