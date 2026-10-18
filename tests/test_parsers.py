"""Both log readers on synthetic lines shaped like the real logs (no real transcripts)."""
import json

from hivemind.parsers import ClaudeCodeParser, CodexParser

MID = "m_0123456789ab"


def cc(type_, content, uuid, **extra):
    return json.dumps({"type": type_, "sessionId": "S1", "uuid": uuid, "cwd": "D:\\graph_hacks",
                       "timestamp": f"2026-10-16T10:00:0{uuid[-1]}Z", "message": {"content": content}, **extra})


def kinds(events):
    return [e["kind"] for e in events]


def test_claude_code_turn_chain_thinking_and_recall():
    p = ClaudeCodeParser()
    ev = p.feed(cc("user", "Users get logged out at random", "u1"))
    assert kinds(ev) == ["session", "message", "session"]
    assert ev[2]["title"] == "Users get logged out at random"
    assert p.feed(cc("assistant", [{"type": "thinking", "thinking": "", "signature": "x"}], "a1")) == []  # hidden thinking
    t = p.feed(cc("assistant", [{"type": "thinking", "thinking": "Check the expiry boundary.", "signature": "x"}], "a2"))
    assert t[0]["kind"] == "thought" and t[0]["fidelity"] == "full"
    assert t[0]["prev"] == "cc:u1" and t[0]["prev_label"] == "Message" and t[0]["turn"] == "cc:u1"
    assert p.feed(cc("assistant", [{"type": "tool_use", "id": "tu1", "name": "mcp__hivemind__recall", "input": {}}], "a3")) == []
    r = p.feed(cc("user", [{"type": "tool_result", "tool_use_id": "tu1", "content": f"[{MID}] finding: tokens pass"}], "u2",
                  toolUseResult={"x": 1}))
    assert r == [{"kind": "recall", "session": "cc:S1", "thought": "cc:a2:0", "memory_ids": [MID]}]
    reply = p.feed(cc("assistant", [{"type": "text", "text": "Found it."}], "a4"))
    assert reply[0]["role"] == "assistant" and reply[0]["prev"] == "cc:a2:0" and reply[0]["prev_label"] == "Thought"


def test_claude_code_skips_noise():
    p = ClaudeCodeParser()
    assert p.feed("not json") == []
    assert p.feed(cc("user", "hi", "u1", isSidechain=True)) == []
    assert p.feed(cc("user", "hi", "u2", isMeta=True)) == []
    ev = p.feed(cc("user", "<system-reminder>ignore</system-reminder>", "u3"))
    assert kinds(ev) == ["session"]  # session is known, but no message
    assert p.feed(json.dumps({"type": "attachment"})) == []


def test_claude_code_resumes_from_saved_state():
    p = ClaudeCodeParser()
    p.feed(cc("user", "first question", "u1"))
    saved = json.loads(json.dumps(p.s))  # what the collector writes to disk
    p2 = ClaudeCodeParser(saved)
    ev = p2.feed(cc("assistant", [{"type": "text", "text": "answer"}], "a1"))
    assert ev[0]["prev"] == "cc:u1" and ev[0]["turn"] == "cc:u1"


def cx(type_, payload, n):
    return json.dumps({"timestamp": f"2026-10-16T11:00:0{n}Z", "type": type_, "payload": payload, "ordinal": n})


def test_codex_messages_summaries_and_recall():
    p = CodexParser()
    assert kinds(p.feed(cx("session_meta", {"id": "X1", "cwd": "D:\\graph_hacks"}, 0))) == ["session"]
    assert p.feed(cx("response_item", {"type": "message", "role": "developer",
                                       "content": [{"type": "input_text", "text": "rules"}]}, 1)) == []
    assert p.feed(cx("response_item", {"type": "message", "role": "user",
                                       "content": [{"type": "input_text", "text": "<environment_context>x"}]}, 2)) == []
    u = p.feed(cx("response_item", {"type": "message", "role": "user", "id": "q1",
                                    "content": [{"type": "input_text", "text": "How do we cache?"}]}, 3))
    assert u[0]["role"] == "user" and u[0]["id"] == "cx:q1"
    assert p.feed(cx("response_item", {"type": "reasoning", "id": "r0", "summary": [], "encrypted_content": "zz"}, 4)) == []
    t = p.feed(cx("response_item", {"type": "reasoning", "id": "r1", "summary": [{"type": "summary_text", "text": "Look at cache.py"}]}, 5))
    assert t[0]["fidelity"] == "summary" and t[0]["prev"] == "cx:q1"
    assert p.feed(cx("response_item", {"type": "function_call", "name": "mcp__hivemind__recall", "call_id": "c1", "arguments": "{}"}, 6)) == []
    rc = p.feed(cx("response_item", {"type": "function_call_output", "call_id": "c1", "output": f"found {MID}"}, 7))
    assert rc[0]["thought"] == "cx:r1:0" and rc[0]["memory_ids"] == [MID]
    a = p.feed(cx("response_item", {"type": "message", "role": "assistant", "id": "a1",
                                    "content": [{"type": "output_text", "text": "An LRU with no TTL."}]}, 8))
    assert a[0]["role"] == "assistant" and a[0]["prev_label"] == "Thought"


def test_codex_ignores_other_tool_calls():
    p = CodexParser()
    p.feed(cx("session_meta", {"id": "X2", "cwd": "D:\\graph_hacks"}, 0))
    p.feed(cx("response_item", {"type": "function_call", "name": "shell", "call_id": "c9"}, 1))
    assert p.feed(cx("response_item", {"type": "function_call_output", "call_id": "c9", "output": MID}, 2)) == []
