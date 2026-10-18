"""Store against a real FalkorDB. Skipped (not faked) when FalkorDB isn't reachable."""
import dataclasses
import uuid

import pytest

from hivemind.config import settings
from hivemind.store import Store

WS = "pytest_" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def store():
    s = Store(dataclasses.replace(settings, workspace=WS))
    try:
        s.ping()
    except Exception as e:
        pytest.skip(f"FalkorDB not reachable ({type(e).__name__}); set FALKORDB_* in .env")
    yield s
    for name in s.db.list_graphs():
        if name.startswith(WS + "__"):
            s.db.select_graph(name).delete()


def turn(sid, n, user, thought, reply, prefix):
    u, t, r = f"{prefix}:u{n}", f"{prefix}:t{n}", f"{prefix}:r{n}"
    return [{"kind": "message", "id": u, "session": sid, "role": "user", "text": user, "ts": f"2026-10-16T10:0{n}:00Z", "turn": u},
            {"kind": "thought", "id": t, "session": sid, "text": thought, "fidelity": "full", "ts": f"2026-10-16T10:0{n}:01Z",
             "turn": u, "prev": u, "prev_label": "Message"},
            {"kind": "message", "id": r, "session": sid, "role": "assistant", "text": reply, "ts": f"2026-10-16T10:0{n}:02Z",
             "turn": u, "prev": t, "prev_label": "Thought"}]


def test_end_to_end_memory_flow(store):
    # Codex chats, finds a bug, saves it from its reply
    events = [{"kind": "session", "id": "cx:S", "cwd": "D:\\graph_hacks", "ts": "2026-10-16T10:00:00Z", "title": "debug login"}]
    events += turn("cx:S", 1, "Users get logged out", "Check the expiry boundary", "The check uses < instead of <=", "cx")
    assert store.ingest("codex", events) == {"sessions": 1, "messages": 2, "thoughts": 1, "recalls": 0}
    assert store.ingest("codex", events)["messages"] == 2  # re-ingest is harmless (MERGE)
    mid = store.remember("codex", "finding", "Expired tokens pass auth: the check uses < instead of <=.",
                         ["token expiry"], src="cx:r1")
    assert store.remember("codex", "finding", "Expired tokens pass auth: the check uses < instead of <=.") == mid  # dedupe

    # Claude recalls it: RECALLED edge from Codex's memory to Claude
    r = store.recall("claude-code", "token expiry")
    assert [m["id"] for m in r["memories"]] == [mid]
    store.ingest("claude-code", [{"kind": "session", "id": "cc:S", "title": "fix", "ts": "2026-10-16T11:00:00Z"}]
                 + turn("cc:S", 1, "Fix the auth bug", "recall found codex's finding", "Fixed", "cc")
                 + [{"kind": "recall", "session": "cc:S", "thought": "cc:t1", "memory_ids": [mid]}])
    t = store.trace(mid)
    assert t["agent"] == "codex" and t["source"]["text"] == "The check uses < instead of <="
    assert [x["text"] for x in t["source"]["turn"]][:2] == ["Users get logged out", "Check the expiry boundary"]
    assert t["recalled_by"][0]["agent"] == "claude-code" and t["recalled_by"][0]["count"] == 1
    assert t["recalled_by"][0]["thoughts"] == ["recall found codex's finding"]
    store.recall("codex", "token")  # an agent recalling its own memory is not a RECALLED edge
    assert len(store.trace(mid)["recalled_by"]) == 1


def test_handoffs_supersede_and_state(store):
    h = store.remember("claude-code", "handoff", "Retry the session once on a 401.", to="antigravity")
    assert "Open handoffs for you" in store.pending_footer("antigravity")
    assert not store.respond("codex", h, "accept")  # only the addressee can answer
    assert store.respond("antigravity", h, "accept", "on it")
    assert store.pending_footer("antigravity") == ""
    old = store.remember("codex", "fact", "API responses are cached in an in-process LRU with no TTL.", ["API cache"])
    new = store.remember("claude-code", "decision", "Move the API cache to Redis with a 5-minute TTL.", ["API cache"], supersedes=old)
    st = store.state()
    assert st["MEMS"][old]["status"] == "superseded" and st["MEMS"][new]["supersedes"] == old
    assert {"AG", "FID", "MEMS", "LOGS"} <= set(st) and st["LOGS"]["codex"][0]["turns"][0]["reply"] == "The check uses < instead of <="


def test_pending_items_and_marking(store):
    items = store.pending_items(50)
    assert any(i["id"] == "cx:r1" for i in items)
    store.mark_extracted("codex", [i["id"] for i in items if i["agent"] == "codex"])
    assert not any(i["agent"] == "codex" for i in store.pending_items(50))
