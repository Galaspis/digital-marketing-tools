"""Conversation loop: one Claude tool-runner call per operator message,
history kept locally, token cost tallied per turn. Same shape as the ads
agent; the system prompt is what differs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import anthropic

from .config import Settings

from agentkit import PriceBook
from agentkit.openai_loop import OpenAIToolLoop

BOOK = PriceBook()  # pricing.json at the hub root; no price lives in code

SYSTEM_PROMPT = """You are the social media desk for a small digital marketing agency, working with one human operator in a web hub.

What you do:
- Turn the operator's idea into ready-to-post content: a Facebook Page post (text, link, photo, video) or an
  Instagram post (photo, reel, story, carousel). Write the caption yourself, in the language the operator
  writes in (Hebrew or English), in the brand's voice if they described one. Hashtags only where they fit,
  never a wall of them. Instagram needs media; it has no text-only posts.
- Save it with create_draft first. Drafts are free and appear in the operator's calendar. Show the final
  text and say where and when it would go.
- Only publish_post / schedule_post when the operator asks to publish or schedule. Each one opens an
  approval card in the hub; if it comes back declined or refused by policy, explain and stop, do not retry.
- Scheduling: Facebook keeps the post itself (10 minutes to 30 days ahead). Instagram cannot, so our server
  publishes it at the time; mention that the hub must be running then. Times without an offset are local.
- Allowed publish targets: {targets}. Anything else can only be drafted.
- Media: public URLs work everywhere. File names refer to the operator's media folder; for Instagram they
  are uploaded to hosting automatically (upload_media does it explicitly). If hosting is not configured,
  say so and ask for a URL.
- Images: generate_image makes a picture from a detailed English prompt and returns a file name to put in
  `media`. Offer it when a post needs a visual and the operator has none. Use medium quality unless asked;
  it costs a few cents per image and there is a daily cap. Describe subject, setting, style, colours.
- Keep answers short and concrete. Never invent metrics or results.
- Mode: {mode}. Now (UTC): {now}."""


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
            targets=", ".join(self.settings.publish_targets) or "(none configured)",
            mode="DRY RUN against a fake Meta; nothing is real" if self.settings.dry_run else "LIVE Meta Graph API",
            now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        )
        # The date changes once a day, so the cached prefix survives a whole session.
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
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        if not self.settings.dry_run:
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}

        runner = self.client.beta.messages.tool_runner(
            model=model, max_tokens=16000, system=self._system(),
            thinking={"type": "adaptive"}, output_config={"effort": self.settings.effort},
            tools=self.tools, messages=self.messages, **kwargs,
        )
        final_text, last = "", None
        for message in runner:
            last = message
            turn.add(message.usage)
            self.messages.append({"role": "assistant", "content": message.content})
            texts = [b.text for b in message.content if b.type == "text" and b.text.strip()]
            if texts:
                final_text = "\n".join(texts)
                if message.stop_reason == "tool_use":
                    self.on_text(final_text)
            if self.on_tool_call is not None:
                for b in message.content:
                    if b.type == "tool_use":
                        self.on_tool_call(b.name, dict(b.input) if isinstance(b.input, dict) else {})
            tool_response = runner.generate_tool_call_response()
            if tool_response is not None:
                self.messages.append(tool_response)

        if last is not None and last.stop_reason == "refusal":
            detail = getattr(last, "stop_details", None)
            final_text = "Claude declined this request" + (f" ({detail.category}: {detail.explanation})" if detail else "") + "."
        elif last is not None and last.stop_reason == "max_tokens":
            final_text += "\n[cut off: hit max_tokens]"

        self.session_usage.input_tokens += turn.input_tokens
        self.session_usage.output_tokens += turn.output_tokens
        self.session_usage.cache_read += turn.cache_read
        self.session_usage.cache_write += turn.cache_write
        self.transcript += [("user", user_text), ("assistant", final_text)]
        return final_text, turn

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
                on_text=lambda t: self.on_text(t), on_tool_call=lambda n, a: self.on_tool_call(n, a) if self.on_tool_call else None,
                parallel_tool_calls=self.settings.dry_run)
        seed = self.transcript if self._oa_loop.previous_response_id is None else None
        text, u = self._oa_loop.send(model_id, user_text, seed=seed)
        turn = Usage(u.input_tokens, u.output_tokens, u.cache_read, u.cache_write)
        self.transcript += [("user", user_text), ("assistant", text)]
        self.session_usage.input_tokens += turn.input_tokens
        self.session_usage.output_tokens += turn.output_tokens
        self.session_usage.cache_read += turn.cache_read
        return text, turn

