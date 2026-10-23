"""The conversation loop: one Claude tool-runner call per operator message,
history kept locally, token cost tallied per turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import anthropic

from .config import Settings

from agentkit import PriceBook
from agentkit.openai_loop import OpenAIToolLoop

BOOK = PriceBook()  # pricing.json at the hub root; no price lives in code

SYSTEM_PROMPT = """You are a Meta Ads operator's assistant, working in a terminal with one human operator.

Scope in this version:
- You may READ any ad account the token can see: accounts, campaigns, ad sets, ads, insights.
- You may CREATE campaigns and ad sets, PAUSE/ACTIVATE/ARCHIVE objects and CHANGE BUDGETS only on the
  sandbox account(s): {write_accounts}. Every write asks the operator for y/n approval in the terminal;
  a refused or declined write comes back to you as an error message. Explain it, do not retry it.
- Real client accounts are read-only. If asked to change one, say that writes there are not unlocked yet.

How to work:
- Numbers come only from tool results. Never estimate a metric. If a value is missing, say so.
- Budgets in the API are minor currency units (agorot, cents). When you show them, convert
  (e.g. 15000 -> 150.00) and name the currency from the account data.
- Before any write, restate exactly what will change (account, object, values) in one short block,
  then call the tool. Campaigns and ad sets are created PAUSED.
- When analysing, lead with the conclusion, then the evidence, then what to do. Compare like with like
  (same date range, same level). Point out anomalies: spend with no results, CTR far from the rest,
  frequency above 3, paused objects still holding budget.
- Answer in the language the operator writes in (Hebrew or English). Keep it short and plain.
- Mode: {mode}."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, u) -> None:
        self.input_tokens += u.input_tokens or 0
        self.output_tokens += u.output_tokens or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

    def usd(self, model: str) -> float:
        return BOOK.text_cost(model, self.input_tokens, self.output_tokens, self.cache_read, self.cache_write)


@dataclass
class Agent:
    settings: Settings
    tools: list
    on_text: Callable[[str], None] = print
    on_tool_call: Callable[[str, dict], None] | None = None
    client: anthropic.Anthropic = field(default_factory=anthropic.Anthropic)
    messages: list = field(default_factory=list)
    session_usage: Usage = field(default_factory=Usage)
    last_model: str = ""
    transcript: list = field(default_factory=list)  # (role, text) pairs, provider-neutral
    _oa_client: object = None
    _oa_loop: object = None

    def _system(self) -> list[dict]:
        text = SYSTEM_PROMPT.format(
            write_accounts=", ".join(self.settings.write_accounts) or "(none configured)",
            mode="DRY RUN against an in-memory fake Meta; nothing here is real"
            if self.settings.dry_run else "LIVE Meta Marketing API",
        )
        # Stable prefix, cached across turns.
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def send(self, user_text: str, model: str | None = None) -> tuple[str, Usage]:
        """Run one operator turn to completion on the provider the chosen model belongs to."""
        key, provider, model_id = self._resolve(model)
        self.last_model = key
        if provider == "openai":
            return self._send_openai(user_text, model_id)
        model = model_id
        if not self.messages and self.transcript:  # joining a conversation that ran on OpenAI
            self.messages = [{"role": r, "content": t} for r, t in self.transcript if t]
        self.messages.append({"role": "user", "content": user_text})
        turn = Usage()

        kwargs: dict = {}
        if self.settings.fallbacks and model in ("claude-opus-5", "claude-fable-5-1"):
            # Server-side refusal fallback: if a safety classifier declines, the API re-runs
            # the same request on a fallback model inside the same call.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        if not self.settings.dry_run:
            # Meta's development tier allows ~1 call / 30 s per ad account; parallel
            # tool calls would just queue up behind the retry sleeps.
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}

        runner = self.client.beta.messages.tool_runner(
            model=model,
            max_tokens=16000,
            system=self._system(),
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.effort},
            tools=self.tools,
            messages=self.messages,
            **kwargs,
        )

        final_text = ""
        last = None
        for message in runner:
            last = message
            turn.add(message.usage)
            # Mirror history: the runner keeps its own copy and does not expose it.
            self.messages.append({"role": "assistant", "content": message.content})
            texts = [b.text for b in message.content if b.type == "text" and b.text.strip()]
            if texts:
                final_text = "\n".join(texts)
                if message.stop_reason == "tool_use":
                    self.on_text(final_text)  # narration before a tool call
            if self.on_tool_call is not None:
                for b in message.content:
                    if b.type == "tool_use":
                        self.on_tool_call(b.name, dict(b.input) if isinstance(b.input, dict) else {})
            tool_response = runner.generate_tool_call_response()
            if tool_response is not None:
                self.messages.append(tool_response)

        if last is not None and last.stop_reason == "refusal":
            detail = getattr(last, "stop_details", None)
            final_text = ("Claude declined this request"
                          + (f" ({detail.category}: {detail.explanation})" if detail else "") + ".")
        elif last is not None and last.stop_reason == "max_tokens":
            final_text += "\n[cut off: hit max_tokens]"

        self._accumulate(turn)
        self.transcript += [("user", user_text), ("assistant", final_text)]
        return final_text, turn

    def _accumulate(self, turn: Usage) -> None:
        self.session_usage.input_tokens += turn.input_tokens
        self.session_usage.output_tokens += turn.output_tokens
        self.session_usage.cache_read += turn.cache_read
        self.session_usage.cache_write += turn.cache_write

    def reset(self) -> None:
        self.messages.clear()
        self.transcript.clear()
        if self._oa_loop is not None:
            self._oa_loop.reset()
    # ------------------------------------------------------------ provider routing

    def _resolve(self, model: str | None) -> tuple[str, str, str]:
        """(price-book key, provider, bare model id) for a picker value or bare id."""
        key = model or self.settings.model
        entry = BOOK.get(key)
        if entry is None:
            return key, "anthropic", key.split("/")[-1]
        return entry["key"], entry["provider"], entry["model"]

    def _openai_client(self):
        if self._oa_client is None:
            from openai import OpenAI

            self._oa_client = OpenAI(api_key=self.settings.openai_api_key)
        return self._oa_client

    def _send_openai(self, user_text: str, model_id: str) -> tuple[str, Usage]:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set, so OpenAI models cannot run.")
        if self._oa_loop is None:
            self._oa_loop = OpenAIToolLoop(
                self._openai_client(), self.tools, self._system()[0]["text"],
                on_text=lambda t: self.on_text(t),
                on_tool_call=lambda n, a: self.on_tool_call(n, a) if self.on_tool_call else None,
                parallel_tool_calls=self.settings.dry_run)
        seed = self.transcript if self._oa_loop.previous_response_id is None else None
        text, u = self._oa_loop.send(model_id, user_text, seed=seed)
        turn = Usage(u.input_tokens, u.output_tokens, u.cache_read, u.cache_write)
        self.transcript += [("user", user_text), ("assistant", text)]
        self.session_usage.input_tokens += turn.input_tokens
        self.session_usage.output_tokens += turn.output_tokens
        self.session_usage.cache_read += turn.cache_read
        return text, turn

