"""The OpenAI loop against a scripted fake client: text -> tool call -> result -> text."""

import json
from types import SimpleNamespace as NS

from agentkit.openai_loop import OpenAIToolLoop, tool_to_openai


class FakeTool:
    name = "add"
    def to_dict(self):
        return {"name": "add", "description": "Add two numbers.", "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}
    def call(self, args):
        return json.dumps({"sum": args["a"] + args["b"]})


def usage(inp, out, cached=0):
    return NS(input_tokens=inp, output_tokens=out, input_tokens_details=NS(cached_tokens=cached))


class FakeResponses:
    def __init__(self):
        self.calls = []
    def create(self, **kw):
        self.calls.append(kw)
        n = len(self.calls)
        if n == 1:  # model narrates then asks for the tool
            return NS(id="r1", usage=usage(100, 20), output=[
                NS(type="reasoning"),
                NS(type="message", content=[NS(type="output_text", text="Let me add those.")]),
                NS(type="function_call", call_id="c1", name="add", arguments=json.dumps({"a": 2, "b": 3})),
            ])
        if n == 2:  # tool result came back; final answer
            assert kw["previous_response_id"] == "r1"
            assert kw["input"] == [{"type": "function_call_output", "call_id": "c1", "output": json.dumps({"sum": 5})}]
            return NS(id="r2", usage=usage(150, 10, cached=90), output=[
                NS(type="message", content=[NS(type="output_text", text="2 + 3 = 5")])])
        # second user turn chains from r2
        assert kw["previous_response_id"] == "r2" and kw["input"][-1]["role"] == "user"
        return NS(id="r3", usage=usage(50, 5), output=[NS(type="message", content=[NS(type="output_text", text="ok")])])


def test_tool_conversion():
    t = tool_to_openai(FakeTool())
    assert t["type"] == "function" and t["name"] == "add" and t["parameters"]["required"] == ["a", "b"]


def test_loop_runs_tools_and_tracks_usage():
    fake = NS(responses=FakeResponses())
    narr, calls = [], []
    loop = OpenAIToolLoop(fake, [FakeTool()], "You add numbers.", on_text=narr.append,
                          on_tool_call=lambda n, a: calls.append((n, a)))
    text, u = loop.send("gpt-5.6-sol", "what is 2+3?", seed=[("user", "hi"), ("assistant", "hello")])
    assert text == "2 + 3 = 5"
    assert narr == ["Let me add those."] and calls == [("add", {"a": 2, "b": 3})]
    assert u.input_tokens == 100 + 60 and u.cache_read == 90 and u.output_tokens == 30 and u.calls == 2
    first = fake.responses.calls[0]
    assert first["input"][:2] == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert first["tools"][0]["name"] == "add" and first["instructions"] == "You add numbers."
    text2, _ = loop.send("gpt-5.6-sol", "thanks")
    assert text2 == "ok"


def test_unknown_tool_and_bad_json_do_not_crash():
    class R:
        n = 0
        def create(self, **kw):
            R.n += 1
            if R.n == 1:
                return NS(id="a", usage=None, output=[NS(type="function_call", call_id="x", name="nope", arguments="{bad")])
            assert "error" in kw["input"][0]["output"]
            return NS(id="b", usage=None, output=[NS(type="message", content=[NS(type="output_text", text="done")])])
    loop = OpenAIToolLoop(NS(responses=R()), [FakeTool()], "x")
    assert loop.send("m", "go")[0] == "done"
