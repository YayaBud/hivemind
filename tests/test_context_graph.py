import json
from pathlib import Path

import pytest

from hivemind.context_graph import ContextGraph


def _graph(tmp_path: Path) -> ContextGraph:
    rows = [
        {"id": "m1", "agent": "codex", "session": "s1", "role": "user", "source_path": "chat.jsonl", "source_line": 1, "text": "Please inspect the build and report the next step."},
        {"id": "m2", "agent": "codex", "session": "s1", "role": "assistant", "source_path": "chat.jsonl", "source_line": 2, "text": "Build passed; I will inspect the graph."},
        {"id": "m3", "agent": "codex", "session": "s1", "role": "user", "source_path": "chat.jsonl", "source_line": 3, "text": "After completion, render the base body only."},
        {"id": "m4", "agent": "codex", "session": "s1", "role": "assistant", "source_path": "chat.jsonl", "source_line": 4, "text": "The old plan is superseded by the new plan."},
    ]
    notes = [{"id": "n1", "agent": "artifact", "session": "plan", "role": "artifact", "source_path": "plan.md", "source_line": 1, "text": "# Build plan\nUse graph edges to preserve chronology."}]
    (tmp_path / "corpus.jsonl").write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text("\n".join(json.dumps(x) for x in notes), encoding="utf-8")
    return ContextGraph.from_directory(tmp_path)


def test_hybrid_follows_latest_request_and_budget(tmp_path):
    graph = _graph(tmp_path)
    result = graph.search("What is the next request?", 128, "hybrid")
    assert result["tokens"] <= 128
    assert "render the base body only" in result["context"]
    assert all(card["source_id"] for card in result["cards"])


def test_deterministic_ids_and_heading_edges(tmp_path):
    first = _graph(tmp_path)
    second = _graph(tmp_path)
    assert [c["id"] for c in first.cards] == [c["id"] for c in second.cards]
    assert any(e["type"] == "HAS_SECTION" for e in first.edges)
    assert all(e["source"] in first.nodes and e["target"] in first.nodes for e in first.edges)


def test_open_validation_and_redaction(tmp_path):
    (tmp_path / "corpus.jsonl").write_text(json.dumps({"id": "x", "text": "token=supersecretvalue", "agent": "a", "session": "s", "role": "user"}), encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text("", encoding="utf-8")
    graph = ContextGraph.from_directory(tmp_path)
    result = graph.open(graph.cards[0]["id"], 128)
    assert "[redacted]" in result["context"]
    with pytest.raises(ValueError): graph.open("missing")
    with pytest.raises(ValueError): graph.search("", 128)
    with pytest.raises(ValueError): graph.search("x", 127)


def test_invalid_json_duplicate_and_media_rejected(tmp_path):
    (tmp_path / "corpus.jsonl").write_text("{\n", encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError): ContextGraph.from_directory(tmp_path)


def test_flat_has_no_traversal_and_hybrid_reports_used_edges(tmp_path):
    graph = _graph(tmp_path)
    flat = graph.search("build", 800, "flat")
    hybrid = graph.search("build", 800, "hybrid")
    assert flat["traversed_edges"] == []
    assert hybrid["traversed_edges"]
    assert all(edge in {e["id"] for e in graph.edges} for edge in hybrid["traversed_edges"])


def test_open_budget_is_exact_and_continuation_is_explicit(tmp_path):
    graph = _graph(tmp_path)
    result = graph.open(graph.cards[0]["id"], 128)
    assert result["tokens"] <= 128
    assert result["node_id"] == graph.cards[0]["id"]
    with pytest.raises(ValueError): graph.open(graph.cards[0]["id"], 128, -1)


def test_context_is_deterministic(tmp_path):
    one = _graph(tmp_path).search("next request", 800, "hybrid")
    two = _graph(tmp_path).search("next request", 800, "hybrid")
    assert one["context"] == two["context"]
    assert one["traversed_edges"] == two["traversed_edges"]


def test_topic_walk_recovers_related_source(tmp_path):
    rows = [
        {"id": "a", "agent": "one", "session": "s", "role": "user", "source_path": "shared.md", "source_line": 2, "text": "# Shared\nanchor zebra"},
        {"id": "b", "agent": "two", "session": "t", "role": "assistant", "source_path": "shared.md", "source_line": 4, "text": "# Shared\nunique evidence"},
    ]
    (tmp_path / "corpus.jsonl").write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text("", encoding="utf-8")
    graph = ContextGraph.from_directory(tmp_path)
    result = graph.search("zebra", 500, "hybrid")
    assert any(c["source_id"] == "b" for c in result["cards"])
    assert len(result["traversed_edges"]) >= 2
    assert all(any(e["id"] == edge for e in graph.edges) for edge in result["traversed_edges"])


def test_augmented_lexical_matching_is_hybrid_only(tmp_path):
    rows = [
        {"id": "r", "agent": "a", "session": "s", "role": "assistant", "source_path": "x", "source_line": 1, "text": "The random seed keeps replay deterministic."},
        {"id": "g", "agent": "a", "session": "s", "role": "assistant", "source_path": "x", "source_line": 2, "text": "Source game identification was identified in the report."},
        {"id": "u", "agent": "a", "session": "s", "role": "assistant", "source_path": "x", "source_line": 3, "text": "Completely unrelated material."},
    ]
    (tmp_path / "corpus.jsonl").write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text("", encoding="utf-8")
    graph = ContextGraph.from_directory(tmp_path)
    hybrid = graph.search("What was the randomness source-game identification?", 500, "hybrid")
    flat = graph.search("What was the randomness source-game identification?", 500, "flat")
    assert {c["source_id"] for c in hybrid["cards"]} >= {"r", "g"}
    assert graph._expanded_query("identification")["identified"] > 0
    assert list(graph._expanded_query("unrelated")) == ["unrelated"]
    assert flat["tokens"] <= 500 and hybrid["tokens"] <= 500


def test_augmented_role_prioritizes_memory_without_dropping_task(tmp_path):
    rows = [{"id": "task", "agent": "a", "session": "s", "role": "agent_task", "source_path": "x", "source_line": 1, "text": "Procedure status report for the build."}]
    notes = [{"id": "memory", "agent": "artifact", "session": "n", "role": "artifact", "source_path": "notes.md", "source_line": 2, "text": "Procedure status is complete and recorded."}]
    (tmp_path / "corpus.jsonl").write_text(json.dumps(rows[0]), encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text(json.dumps(notes[0]), encoding="utf-8")
    graph = ContextGraph.from_directory(tmp_path)
    result = graph.search("procedure status", 500, "hybrid")
    ids = [c["source_id"] for c in result["cards"]]
    assert ids[0] == "memory"
    assert result["cards"][0]["kind"] == "memory"
    assert "task" in ids


def test_provenance_safe_next_and_explicit_topic_walk(tmp_path):
    rows = [
        {"id": "m1", "agent": "a", "session": "s", "role": "assistant", "source_path": "one.md", "source_line": 1, "text": "anchor", "topics": ["shared subject"]},
        {"id": "m3", "agent": "b", "session": "t", "role": "assistant", "source_path": "three.md", "source_line": 3, "text": "peer evidence", "topics": ["shared subject"]},
    ]
    notes = [{"id": "m2", "agent": "a", "session": "s", "role": "artifact", "source_path": "two.md", "source_line": 2, "text": "unrelated"}, {"id": "chunk", "agent": "a", "session": "s", "role": "artifact", "source_path": "plan.md", "source_line": 4, "text": "same source chunk", "topics": ["shared subject"]}]
    (tmp_path / "corpus.jsonl").write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text("\n".join(json.dumps(x) for x in notes), encoding="utf-8")
    graph = ContextGraph.from_directory(tmp_path)
    m1 = next(c["id"] for c in graph.cards if c["source_id"] == "m1"); m2 = next(c["id"] for c in graph.cards if c["source_id"] == "m2")
    assert not any(e["type"] == "NEXT" and {e["source"], e["target"]} == {m1, m2} for e in graph.edges)
    result = graph.search("anchor", 500, "hybrid")
    assert any(c["source_id"] == "m3" for c in result["cards"])
    assert any(e.startswith("HAS_TOPIC:") for e in result["traversed_edges"])


def test_long_source_chunks_chain_but_unrelated_memories_do_not(tmp_path):
    long_text = " ".join(["chunkword"] * 700)
    notes = [
        {"id": "long", "agent": "artifact", "session": "plan", "role": "artifact", "source_path": "same.md", "source_line": 1, "text": long_text},
        {"id": "other", "agent": "artifact", "session": "plan", "role": "artifact", "source_path": "other.md", "source_line": 2, "text": "other memory"},
    ]
    (tmp_path / "corpus.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text("\n".join(json.dumps(x) for x in notes), encoding="utf-8")
    graph = ContextGraph.from_directory(tmp_path)
    chunks = [c for c in graph.cards if c["source_id"] == "long"]
    assert len(chunks) > 2
    assert all(any(e["type"] == "NEXT" and e["source"] == left["id"] and e["target"] == right["id"] for e in graph.edges) for left, right in zip(chunks, chunks[1:]))
    other = next(c for c in graph.cards if c["source_id"] == "other")
    assert not any(e["type"] == "NEXT" and other["id"] in {e["source"], e["target"]} and all(c["id"] not in {e["source"], e["target"]} for c in chunks) for e in graph.edges)

