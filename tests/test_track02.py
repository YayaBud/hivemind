from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest

from hivemind.config import settings
from hivemind.shared_context import SharedContextGraph
from hivemind.store import Store


def _live_store(prefix):
    local = replace(settings, workspace=prefix + uuid.uuid4().hex[:8],
                    falkor_host="127.0.0.1", falkor_port=6379,
                    falkor_username=None, falkor_password=None, falkor_ssl=False)
    store = Store(local)
    try:
        store.ping()
    except Exception as exc:
        pytest.skip(f"local FalkorDB unavailable: {exc.__class__.__name__}")
    return store


def _cleanup(store):
    names = [store.gname(), *(store.gname(a) for a in ("codex", "claude-code", "antigravity"))]
    try:
        existing = set(store.db.list_graphs())
        for name in names:
            if name in existing and name.startswith(store.ws + "__"):
                store.db.select_graph(name).delete()
    except Exception:
        pass


def test_store_persistence_import_idempotency_and_procedure():
    store = _live_store("track02-persist-")
    try:
        long_text = "persistent imported text " + ("x" * 5400)
        records = [{"id": "r", "agent": "codex", "session": "s", "role": "user", "text": "imported record"}]
        notes = [{"id": "n", "agent": "artifact", "text": long_text, "kind": "howto", "memory_type": "procedural"}]
        first = store.import_context(records, notes)
        second = store.import_context(records, notes)
        assert first["records"] == second["records"] == 1
        assert first["notes"] == second["notes"] == 1
        snap = store.context_snapshot()
        assert [r["source_id"] for r in snap["records"]].count("r") == 1
        imported = next(n for n in snap["notes"] if n["source_id"] == "n")
        assert imported["text"] == long_text
        assert imported["kind"] == "howto" and imported["memory_type"] == "procedural"
        assert Store(store.s).context_snapshot()["notes"]
    finally:
        _cleanup(store)


def test_handoff_lifecycle_wrong_agent_and_done():
    store = _live_store("track02-handoff-")
    try:
        mid = store.remember("codex", "handoff", "lifecycle handoff", to="claude-code")
        assert not store.respond("antigravity", mid, "accept")
        assert store.respond("claude-code", mid, "accept")
        assert store.sync("claude-code")["handoffs"]
        assert store.respond("claude-code", mid, "done")
    finally:
        _cleanup(store)


def test_supersedes_status_and_trace():
    store = _live_store("track02-super-")
    try:
        old = store.remember("codex", "fact", "old fact")
        new = store.remember("codex", "fact", "new fact", supersedes=old)
        assert store.trace(old)["status"] == "superseded"
        assert store.trace(new)["status"] == "active"
        assert store.trace(new)["text"] == "new fact"
    finally:
        _cleanup(store)


def test_workspace_isolation_and_shared_cache_revision():
    one, two = _live_store("track02-one-"), _live_store("track02-two-")
    try:
        one.remember("codex", "fact", "tenant one fact")
        assert not any(n["text"] == "tenant one fact" for n in two.context_snapshot()["notes"])
        graph = SharedContextGraph(one)
        first = graph.search("tenant one", 128)
        one.remember("codex", "fact", "tenant one later fact")
        second = graph.search("later", 128)
        assert second["cache_revision"] > first["cache_revision"]
        assert "tenant one later fact" in second["context"]
    finally:
        _cleanup(one)
        _cleanup(two)


def test_concurrent_dedupe_four_writers_one_memory():
    store = _live_store("track02-dedupe-")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: store.remember("codex", "fact", "same concurrent fact"), range(4)))
        assert len(ids) == 4 and len(set(ids)) == 1
        assert len([n for n in store.context_snapshot()["notes"] if n["text"] == "same concurrent fact"]) == 1
    finally:
        _cleanup(store)


def test_concurrent_distinct_writes_readers_and_provenance():
    store = _live_store("track02-concurrent-")
    try:
        store.ingest("codex", [{"kind": "session", "id": "source-session", "title": "source", "ts": None},
                               {"kind": "message", "id": "source-msg", "role": "user", "text": "source provenance", "session": "source-session", "ts": None}])
        def write(i):
            return store.remember("codex", "fact", f"distinct fact {i}", src="source-msg")
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(write, i) for i in range(32)]
            readers = [pool.submit(store.context_snapshot) for _ in range(2)]
            ids = [f.result() for f in futures]
            [f.result() for f in readers]
        assert len(set(ids)) == 32
        selected = [n for n in store.context_snapshot()["notes"] if n["text"].startswith("distinct fact ")]
        assert len(selected) == 32 and all(n["agent"] == "codex" for n in selected)
        assert store.trace(ids[0])["source"]["text"] == "source provenance"
    finally:
        _cleanup(store)


def test_session_next_edges_timestamps_and_no_thought_import():
    store = _live_store("track02-turns-")
    try:
        records = [
            {"id": "u1", "agent": "antigravity", "session": "session-1", "role": "user", "source_step": 1, "text": "first request"},
            {"id": "a1", "agent": "antigravity", "session": "session-1", "role": "assistant", "source_step": 2, "text": "first answer"},
            {"id": "u2", "agent": "antigravity", "session": "session-1", "role": "user", "source_step": 3, "text": "second request"},
            {"id": "a2", "agent": "antigravity", "session": "session-1", "role": "assistant", "source_step": 4, "text": "second answer"},
        ]
        store.import_context(records, [])
        assert all(row.get("timestamp") is None for row in store.context_snapshot()["records"])
        rows = store.graph("antigravity").query(
            "MATCH (m:Message {session: $session}) RETURN m.turn, m.role, m.text ORDER BY m.source_step",
            {"session": "session-1"}).result_set
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("u1", "user", "first request"), ("u1", "assistant", "first answer"),
            ("u2", "user", "second request"), ("u2", "assistant", "second answer")]
        edges = store.graph("antigravity").query("MATCH ()-[r:NEXT]->() RETURN count(r)").result_set
        assert int(edges[0][0]) == 3
        store.import_context([{"id": "msg", "agent": "codex", "session": "s", "role": "assistant", "text": "public message"}], [])
        assert store.graph("codex").query("MATCH (t:Thought) RETURN t.id").result_set == []
    finally:
        _cleanup(store)


def test_falkordb_shared_topic_cross_agent_retrieval():
    store = _live_store("track02-topic-")
    try:
        first = store.remember("codex", "fact", "alpha-anchor fact", topics=["shared track topic"])
        second = store.remember("claude-code", "fact", "other-agent evidence", topics=["shared track topic"])
        assert first != second
        result = SharedContextGraph(store).search("alpha-anchor", 800)
        assert any(card["source_id"] == second or card["text"] == "other-agent evidence" for card in result["cards"])
        assert any(edge.startswith("HAS_TOPIC:") for edge in result["traversed_edges"])
    finally:
        _cleanup(store)


def test_falkordb_import_infers_missing_turns():
    store = _live_store("track02-turns2-")
    try:
        store.import_context([
            {"id": "u1", "agent": "antigravity", "session": "session-1", "role": "user", "source_step": 1, "text": "first request"},
            {"id": "a1", "agent": "antigravity", "session": "session-1", "role": "assistant", "source_step": 2, "text": "first answer"},
            {"id": "u2", "agent": "antigravity", "session": "session-1", "role": "user", "source_step": 3, "text": "second request"},
            {"id": "a2", "agent": "antigravity", "session": "session-1", "role": "assistant", "source_step": 4, "text": "second answer"},
        ], [])
        assert all(row.get("timestamp") is None for row in store.context_snapshot()["records"])
        state = store.state()
        turns = state["LOGS"]["antigravity"][0]["turns"]
        assert [(t["user"], t["reply"]) for t in turns] == [
            ("first request", "first answer"), ("second request", "second answer")]
    finally:
        _cleanup(store)
