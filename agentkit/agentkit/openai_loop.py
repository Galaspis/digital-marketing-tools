"""The agent loop on OpenAI's Responses API, with the same tools the Claude
loop uses (the `@beta_tool` functions: name, description, input_schema, call).

Verified against developers.openai.com on 2026-09-06: `client.responses.create`
with `tools=[{type: "function", name, description, parameters}]`; the model's
tool requests arrive as output items of type `function_call` (call_id, name,
arguments as a JSON string); results go back as input items of type
`function_call_output` (call_id, output); the conversation continues with
`previous_response_id`, so reasoning items never have to be replayed by hand.

Approval gates live inside the tools (the Guard), so this loop needs no
special hook: a declined action simply returns as the tool's text result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class LoopUsage:
    input_tokens: int = 0     # uncached input
    output_tokens: int = 0
    cache_read: int = 0       # cached input tokens (billed at the cached rate)
    cache_write: int = 0      # OpenAI has no separate write charge; kept for symmetry
    calls: int = 0

    def add(self, usage: Any) -> None:
        if usage is None:
            return
        cached = 0
        details = getattr(usage, "input_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        total_in = getattr(usage, "input_tokens", 0) or 0
        self.input_tokens += max(0, total_in - cached)
        self.cache_read += cached
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.calls += 1


def tool_to_openai(tool: Any) -> dict[str, Any]:
    """Convert a Claude-style tool (to_dict() -> name/description/input_schema) for OpenAI."""
    d = tool.to_dict() if hasattr(tool, "to_dict") else dict(tool)
    return {"type": "function", "name": d["name"], "description": d.get("description", ""),
            "parameters": d.get("input_schema") or {"type": "object", "properties": {}}}


class OpenAIToolLoop:
    def __init__(self, client: Any, tools: list, instructions: str,
                 on_text: Callable[[str], None] | None = None,
                 on_tool_call: Callable[[str, dict], None] | None = None,
                 parallel_tool_calls: bool = True, max_iterations: int = 24) -> None:
        self.client = client
        self.tools = {t.name: t for t in tools}
        self.oa_tools = [tool_to_openai(t) for t in tools]
        self.instructions = instructions
        self.on_text = on_text
        self.on_tool_call = on_tool_call
        self.parallel = parallel_tool_calls
        self.max_iterations = max_iterations
        self.previous_response_id: str | None = None

    def reset(self) -> None:
        self.previous_response_id = None

    def _call_tool(self, name: str, arguments: str) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"arguments were not valid JSON: {e}"})
        if self.on_tool_call:
            self.on_tool_call(name, args if isinstance(args, dict) else {})
        try:
            out = tool.call(args)
        except Exception as e:  # a tool bug must not kill the turn
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)

    def send(self, model: str, user_text: str, seed: list[tuple[str, str]] | None = None) -> tuple[str, LoopUsage]:
        """One operator turn. `seed` (role, text) pairs are prepended when this
        provider joins a conversation that started on the other one."""
        usage = LoopUsage()
        input_items: list[dict[str, Any]] = []
        if seed and self.previous_response_id is None:
            input_items += [{"role": r, "content": t} for r, t in seed if t]
        input_items.append({"role": "user", "content": user_text})

        final_text = ""
        for _ in range(self.max_iterations):
            kwargs: dict[str, Any] = {"model": model, "instructions": self.instructions, "input": input_items,
                                      "tools": self.oa_tools, "parallel_tool_calls": self.parallel}
            if self.previous_response_id:
                kwargs["previous_response_id"] = self.previous_response_id
            resp = self.client.responses.create(**kwargs)
            usage.add(getattr(resp, "usage", None))
            self.previous_response_id = resp.id

            calls, texts = [], []
            for item in resp.output:
                t = getattr(item, "type", "")
                if t == "function_call":
                    calls.append(item)
                elif t == "message":
                    for c in getattr(item, "content", []) or []:
                        if getattr(c, "type", "") == "output_text" and getattr(c, "text", ""):
                            texts.append(c.text)
            if texts:
                final_text = "\n".join(texts)
                if calls and self.on_text:
                    self.on_text(final_text)  # narration before tool calls
            if not calls:
                break
            input_items = [{"type": "function_call_output", "call_id": c.call_id,
                            "output": self._call_tool(c.name, c.arguments)} for c in calls]
        else:
            final_text = (final_text + "\n[stopped: too many tool rounds in one turn]").strip()
        return final_text, usage
