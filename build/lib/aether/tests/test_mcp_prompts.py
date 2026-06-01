"""MCP "/" prompts tests (spec §驗收 Agent 必做 #6).

Proves: 6 prompts register; READ prompts (who/poll/transcript) pre-fetch via the
bridge; WRITE prompts (ask/discuss/stop) return an instruction string and DO NOT
perform any bus write at render time (the double-send safety property, C5)."""
import asyncio

import pytest

from aether.mcp_server import build_server


class SpyBridge:
    """Records read calls; raises if a write method is called (must not happen at render)."""
    def __init__(self):
        self.calls = []

    def list_bodies(self):
        self.calls.append("list_bodies")
        return {"me": "p-mcp", "bodies": [
            {"id": "genesis", "description": "swift", "capabilities": ["swift"], "online": True}],
            "note": "note"}

    def poll(self, thread):
        self.calls.append(("poll", thread))
        return {"thread": thread, "status": "reply_received",
                "new_replies": [{"from": "genesis", "hop": 1, "text": "order_id"}]}

    def transcript(self, thread):
        self.calls.append(("transcript", thread))
        return {"thread": thread, "hops": [
            {"hop": 0, "from": "a", "to": "b", "intent": "ask", "text": "q"}], "ended": None}

    # writes — must NEVER be called during prompt render
    def ask(self, *a, **k):
        raise AssertionError("bridge.ask must not be called at prompt render (double-send risk)")

    def discuss(self, *a, **k):
        raise AssertionError("bridge.discuss must not be called at prompt render")

    def control(self, *a, **k):
        raise AssertionError("bridge.control must not be called at prompt render")


def _render(mcp, name, args):
    return asyncio.run(mcp.get_prompt(name, args))


def _text(result) -> str:
    # GetPromptResult.messages[*].content.text — be tolerant of structure
    parts = []
    for m in result.messages:
        c = m.content
        parts.append(getattr(c, "text", str(c)))
    return "\n".join(parts)


def test_all_six_prompts_registered():
    mcp = build_server(SpyBridge())
    names = {p.name for p in asyncio.run(mcp.list_prompts())}
    assert names == {"who", "ask", "poll", "discuss", "transcript", "stop"}


def test_read_prompts_prefetch_live_data():
    spy = SpyBridge()
    mcp = build_server(spy)
    assert "genesis" in _text(_render(mcp, "who", {}))
    assert "list_bodies" in spy.calls
    assert "order_id" in _text(_render(mcp, "poll", {"thread": "t1"}))
    assert ("poll", "t1") in spy.calls
    assert "hop 0" in _text(_render(mcp, "transcript", {"thread": "t2"}))


def test_ask_is_instruction_only():
    # render must NOT call bridge.ask; must return an instruction naming the tool
    spy = SpyBridge()
    mcp = build_server(spy)
    txt = _text(_render(mcp, "ask", {"to": "genesis", "question": "what field?"}))
    assert "aether_ask" in txt and "genesis" in txt and "what field?" in txt
    assert "/mcp__aether__poll" in txt          # hands off to poll, no loop
    assert spy.calls == []                       # bridge.ask never invoked


def test_discuss_and_stop_are_instruction_only():
    spy = SpyBridge()
    mcp = build_server(spy)
    d = _text(_render(mcp, "discuss", {"from_project": "a", "to_project": "b", "topic": "x"}))
    assert "aether_discuss" in d and spy.calls == []
    s = _text(_render(mcp, "stop", {"thread": "t9"}))
    assert "terminate" in s and "aether_control" in s and spy.calls == []
