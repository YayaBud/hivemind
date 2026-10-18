"""Build and evaluate a small, offline graph retriever for the frozen corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_history import _split_records, format_context, retrieve, token_count
from scripts.evaluate_history import CASES, SYSTEM

BUDGET = 1800
AGENTS = ("claude", "codex", "antigravity")


def _load(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _position(record: dict[str, Any], fallback: int) -> tuple[int, int]:
    value = record.get("source_line", record.get("source_step"))
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, fallback)


def _timestamp(record: dict[str, Any]) -> datetime | None:
    value = record.get("timestamp")
    if not value or value == "time unknown":
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _edge(kind: str, source: str, target: str) -> dict[str, str]:
    return {"id": f"{kind}:{source}->{target}", "type": kind, "source": source, "target": target}


def build_graph(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, str]]], dict[str, dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    edge_by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_id: dict[str, dict[str, Any]] = {}
    sessions: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    agent_seen: set[str] = set()
    for index, record in enumerate(records):
        agent = str(record.get("agent", "unknown"))
        session = str(record.get("session", "unknown"))
        agent_id = f"agent:{agent}"
        session_id = f"session:{agent}:{session}"
        message_id = f"message:{record['id']}"
        if agent not in agent_seen:
            nodes.append({"id": agent_id, "type": "Agent", "agent": agent})
            agent_seen.add(agent)
        if not any(n["id"] == session_id for n in nodes):
            nodes.append({"id": session_id, "type": "Session", "agent": agent, "session": session})
            edges.append(_edge("HAS_SESSION", agent_id, session_id))
        node = {"id": message_id, "type": "Message", "agent": agent, "session": session,
                "role": record.get("role", ""), "source_id": record["id"],
                "source_path": record.get("source_path", ""),
                "source_line": record.get("source_line"), "source_step": record.get("source_step"),
                "timestamp": record.get("timestamp", "time unknown"), "text": record["text"]}
        nodes.append(node)
        by_id[record["id"]] = record
        sessions[(agent, session)].append((index, record))
        edges.append(_edge("HAS_MESSAGE", session_id, message_id))
    for (agent, session), entries in sessions.items():
        ordered = sorted(entries, key=lambda pair: _position(pair[1], pair[0]))
        for (_, previous), (_, current) in zip(ordered, ordered[1:]):
            edge = _edge("NEXT", f"message:{previous['id']}", f"message:{current['id']}")
            edges.append(edge)
            edge_by_source[previous["id"]].append(edge)
    graph = {"schema": "graph-traversal-v1", "nodes": nodes, "edges": edges,
             "provenance": {"records": len(records), "source": "corpus.jsonl"}}
    return graph, edge_by_source, by_id


def _named_agents(question: str) -> list[str]:
    low = question.lower()
    return [agent for agent in AGENTS if re.search(rf"\b{re.escape(agent)}\b", low)]


def _latest_pair(records: list[dict[str, Any]], agent: str) -> list[dict[str, Any]]:
    sessions: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.get("agent") == agent:
            sessions[str(record.get("session", "unknown"))].append((index, record))
    def session_key(items: list[tuple[int, dict[str, Any]]]) -> tuple[int, Any, int]:
        dated = [(stamp, idx) for idx, rec in items if (stamp := _timestamp(rec)) is not None]
        if dated:
            return (1, max(stamp for stamp, _ in dated), max(idx for _, idx in dated))
        return (0, max(_position(rec, idx) for idx, rec in items), max(idx for idx, _ in items))
    if not sessions:
        return []
    items = max(sessions.values(), key=session_key)
    ordered = [rec for idx, rec in sorted(items, key=lambda pair: _position(pair[1], pair[0]))]
    result = []
    for role in ("user", "assistant"):
        matches = [rec for rec in ordered if rec.get("role") == role]
        if matches:
            result.append(matches[-1])
    return result


def _graph_latest_pair(records: list[dict[str, Any]], graph: dict[str, Any], agent: str) -> list[dict[str, Any]]:
    """Resolve the latest pair through Agent -> Session -> Message -> NEXT graph links."""
    by_id = {record["id"]: record for record in records}
    edges = graph["edges"]
    sessions: dict[str, list[str]] = {}
    for edge in edges:
        if edge["type"] == "HAS_SESSION" and edge["source"] == f"agent:{agent}":
            session_id = edge["target"]
            message_ids = [e["target"].removeprefix("message:") for e in edges
                           if e["type"] == "HAS_MESSAGE" and e["source"] == session_id]
            sessions[session_id] = message_ids
    if not sessions:
        return []
    def key(session_id: str) -> tuple[int, Any, int]:
        items = [by_id[mid] for mid in sessions[session_id] if mid in by_id]
        dated = [_timestamp(r) for r in items if _timestamp(r) is not None]
        return (1 if dated else 0, max(dated, default=datetime.min),
                max((_position(r, i) for i, r in enumerate(items)), default=(0, 0)))
    session_id = max(sessions, key=key)
    message_ids = set(sessions[session_id])
    ordered = []
    incoming = {e["target"].removeprefix("message:"): e for e in edges if e["type"] == "NEXT"}
    starts = [mid for mid in message_ids if mid not in incoming]
    current = starts[0] if len(starts) == 1 else None
    while current and current in message_ids:
        ordered.append(by_id[current])
        next_edge = next((e for e in edges if e["type"] == "NEXT" and e["source"] == f"message:{current}" and e["target"].removeprefix("message:") in message_ids), None)
        current = next_edge["target"].removeprefix("message:") if next_edge else None
    if len(ordered) != len(message_ids):
        warnings.warn(f"invalid NEXT chain for {session_id}; using source-order fallback", RuntimeWarning)
        ordered = sorted((by_id[mid] for mid in message_ids if mid in by_id),
                         key=lambda record: _position(record, 0))
    return [matches[-1] for role in ("user", "assistant") if (matches := [r for r in ordered if r.get("role") == role])]


def _trace_text(trace: list[str]) -> str:
    kinds = [edge.split(":", 1)[0] for edge in trace]
    return "Traversal: " + (" > ".join(kinds) if kinds else "none")


def retrieve_graph(records: list[dict[str, Any]], graph: dict[str, Any], edge_by_source: dict[str, list[dict[str, str]]], question: str) -> dict[str, Any]:
    chunks = _split_records(records, 600)
    original_chunks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    record_by_id = {record["id"]: record for record in records}
    for chunk in chunks:
        original_chunks[chunk["id"].split("#chunk", 1)[0]].append(chunk)
    ranked = retrieve(records, question, 900)
    selected: list[dict[str, Any]] = []
    traces: dict[str, list[str]] = {}

    def add_chunk(chunk: dict[str, Any], trace: list[str]) -> None:
        if chunk["id"] not in {r["id"] for r in selected}:
            selected.append(chunk)
            traces[chunk["id"]] = list(trace)

    def add_original(record: dict[str, Any], trace: list[str]) -> None:
        for chunk in original_chunks.get(record["id"], [record]):
            add_chunk(chunk, trace)

    for agent in _named_agents(question):
        for record in _graph_latest_pair(records, graph, agent):
            session_id = f"session:{agent}:{record.get('session')}"
            message_id = f"message:{record['id']}"
            path = [f"HAS_SESSION:agent:{agent}->{session_id}", f"HAS_MESSAGE:{session_id}->{message_id}"]
            add_original(record, path)
    for record in ranked:
        original_id = record["id"].split("#chunk", 1)[0]
        source = record_by_id.get(original_id)
        if source:
            # Retain the actual ranked chunk; the original is only the graph anchor.
            add_chunk(record, [f"LEXICAL_SEED:message:{original_id}"])
    # Expand each lexical seed by one chronological predecessor and successor.
    for record in ranked:
        original_id = record["id"].split("#chunk", 1)[0]
        for edge in edge_by_source.get(original_id, []):
            target_id = edge["target"].removeprefix("message:")
            target = record_by_id.get(target_id)
            if target:
                add_original(target, [edge["id"]])
        predecessor = next((e for edges in edge_by_source.values() for e in edges
                            if e["type"] == "NEXT" and e["target"] == f"message:{original_id}"), None)
        if predecessor:
            target_id = predecessor["source"].removeprefix("message:")
            target = record_by_id.get(target_id)
            if target:
                add_original(target, [predecessor["id"]])

    kept: list[dict[str, Any]] = []
    kept_trace: dict[str, list[str]] = {}
    for record in selected:
        candidate = kept + [record]
        trace_lines = [_trace_text(traces[r["id"]]) for r in candidate]
        context = format_context(candidate) + "\n\n" + "\n".join(trace_lines)
        if token_count(context) <= BUDGET:
            kept.append(record)
            kept_trace[record["id"]] = traces[record["id"]]
    context = format_context(kept)
    trace = [_trace_text(kept_trace[r["id"]]) for r in kept]
    context = (context + "\n\n" + "\n".join(trace)).strip()
    return {"context": context, "source_ids": [r["id"] for r in kept],
            "source_provenance": [{"id": r["id"], "agent": r.get("agent"), "session": r.get("session"),
                                   "role": r.get("role"), "source_path": r.get("source_path"),
                                   "source_line": r.get("source_line"), "source_step": r.get("source_step"),
                                   "timestamp": r.get("timestamp", "time unknown")} for r in kept],
            "tokens": token_count(context), "traversed_edges": [e for r in kept for e in kept_trace[r["id"]]]}


def _self_test() -> None:
    rows = [
        {"id": "x:2", "agent": "claude", "session": "s", "role": "assistant", "source_path": "x", "source_line": 2, "text": "answer", "timestamp": "time unknown"},
        {"id": "x:1", "agent": "claude", "session": "s", "role": "user", "source_path": "x", "source_line": 1, "text": "question", "timestamp": "time unknown"},
        {"id": "x:3", "agent": "claude", "session": "s", "role": "user", "source_path": "x", "source_line": 3, "text": "follow up", "timestamp": "time unknown"},
    ]
    graph, edge_by_source, _ = build_graph(rows)
    next_edges = [e for e in graph["edges"] if e["type"] == "NEXT"]
    assert len(next_edges) == 2 and next_edges[1]["target"] == "message:x:3"
    assert len({e["id"] for e in graph["edges"]}) == len(graph["edges"])
    assert all(e["source"] != e["target"] for e in graph["edges"])
    endpoints = {node["id"] for node in graph["nodes"]}
    assert all(e["source"] in endpoints and e["target"] in endpoints for e in graph["edges"])
    assert all(r.get("timestamp") == "time unknown" for r in rows)
    result = retrieve_graph(rows, graph, edge_by_source, "question")
    assert result["tokens"] <= BUDGET
    shuffled = [rows[2], rows[0], rows[1]]
    shuffled_graph, shuffled_edges, _ = build_graph(shuffled)
    pair = _graph_latest_pair(shuffled, shuffled_graph, "claude")
    assert [record["id"] for record in pair] == ["x:3", "x:2"]
    pair_again = _graph_latest_pair(shuffled, shuffled_graph, "claude")
    assert [record["id"] for record in pair] == [record["id"] for record in pair_again]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        _self_test(); print("self-test passed"); return 0
    if args.input is None:
        print("error: --input is required unless --self-test is used", file=sys.stderr); return 2
    if not args.input.is_absolute() or not args.input.is_dir():
        print("error: --input must be an existing absolute directory", file=sys.stderr); return 2
    corpus_path = args.input / "corpus.jsonl"
    records = _load(corpus_path)
    started = time.perf_counter()
    graph, edge_by_source, _ = build_graph(records)
    graph["provenance"].update({"corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
                                 "build_ms": round((time.perf_counter() - started) * 1000, 3)})
    (args.input / "graph_index.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    rows: list[dict[str, Any]] = []
    blocks: list[str] = []
    for case_id, question, _ in CASES:
        started = time.perf_counter()
        result = retrieve_graph(records, graph, edge_by_source, question)
        result.update({"id": case_id, "question": question, "latency_ms": round((time.perf_counter() - started) * 1000, 3)})
        rows.append(result)
        blocks.append(f"{case_id}: {question}\nContext:\n{result['context'] or '[none]'}")
    prompt = SYSTEM + "\n\n" + "\n\n".join(blocks)
    assert all(row["tokens"] <= BUDGET for row in rows)
    assert "gold_rubric" not in prompt
    c7 = next(row for row in rows if row["id"] == "C7")
    expected = "render me the base body only this one only"
    print(json.dumps({"cases": len(rows), "prompt_tokens": token_count(prompt),
                      "c7_contains_latest_user": expected in c7["context"],
                      "graph_nodes": len(graph["nodes"]), "graph_edges": len(graph["edges"]),
                      "outputs": {"graph": str(args.input / 'graph_index.json'),
                                  "results": str(args.input / 'graph_retrieval_results.json'),
                                  "prompt": str(args.input / 'prompts' / 'graph.txt')}}))
    (args.input / "graph_retrieval_results.json").write_text(json.dumps({"budget_tokens": BUDGET, "cases": rows,
                                                                            "prompt_tokens": token_count(prompt),
                                                                            "graph_build_ms": graph["provenance"]["build_ms"]}, indent=2, ensure_ascii=False), encoding="utf-8")
    prompt_dir = args.input / "prompts"; prompt_dir.mkdir(exist_ok=True)
    (prompt_dir / "graph.txt").write_text(prompt, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
