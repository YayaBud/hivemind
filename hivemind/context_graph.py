"""Deterministic, local hybrid retrieval over a frozen corpus and notes snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tiktoken
except ImportError:  # pragma: no cover - the project snapshot supplies tiktoken
    tiktoken = None

from .redact import redact

_WORD = re.compile(r"[\w][\w'-]*", re.UNICODE)
_STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "that", "the", "to", "was", "were", "with"}
_QUERY_STOP = {"what", "how", "which", "why", "did", "does", "keeps", "remains", "recorded", "method"}
_MEDIA = re.compile(r"data:(?:image|audio|video)/|base64,[A-Za-z0-9+/]{80,}", re.I)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", re.M)
_LATEST = re.compile(r"\b(latest|next|follow[- ]?up|asked|request|todo)\b", re.I)
_AGENT_ALIASES = {"claude code": "claude-code", "claude-code": "claude-code", "codex": "codex", "antigravity": "antigravity", "claude": "claude-code"}


def _tokens(text: str) -> list[str]:
    return [x.lower() for x in _WORD.findall(text) if x.lower() not in _STOP]


def _stamp(value: Any) -> datetime | None:
    if not value or str(value).lower() in {"time unknown", "unknown", "none"}:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _pos(row: dict[str, Any], fallback: int) -> tuple[int, int]:
    for key in ("source_line", "source_step", "line", "step"):
        try:
            return (0, int(row[key]))
        except (KeyError, TypeError, ValueError):
            pass
    return (1, fallback)


def _id(source: str, ordinal: int) -> str:
    return "n_" + hashlib.sha256(f"{source}\0{ordinal}".encode()).hexdigest()[:12]


def _edge(kind: str, source: str, target: str, **extra: Any) -> dict[str, Any]:
    out = {"id": f"{kind}:{source}->{target}", "type": kind, "source": source, "target": target}
    out.update(extra)
    return out


class ContextGraph:
    """Immutable local index built from explicit ``corpus.jsonl`` and ``notes.jsonl``."""

    def __init__(self, records: list[dict[str, Any]], notes: list[dict[str, Any]]):
        if tiktoken is None: raise ValueError("tiktoken is required for exact token budgets")
        seen_ids: set[str] = set()
        for row in list(records) + list(notes):
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or not isinstance(row.get("text"), str) or not row["text"]:
                raise ValueError("each record needs nonempty string id and text")
            if row["id"] in seen_ids: raise ValueError("duplicate id")
            if _MEDIA.search(row["text"]): raise ValueError("media/base64 text rejected")
            seen_ids.add(row["id"])
        self._lock = threading.RLock()
        self.records = tuple(dict(r) for r in records)
        self.notes = tuple(dict(r) for r in notes)
        self.cards: list[dict[str, Any]] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._adj: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._by_id: dict[str, dict[str, Any]] = {}
        self._node_by_id: dict[str, dict[str, Any]] = {}
        self._postings: dict[str, set[str]] = defaultdict(set)
        self._lengths: dict[str, int] = {}
        self._term_counts: dict[str, Counter[str]] = {}
        self._avglen = 0.0
        self._order: dict[str, int] = {}
        self._vocabulary: tuple[str, ...] = ()
        self._expansion_cache: dict[str, tuple[str, ...]] = {}
        self._build()

    @classmethod
    def from_directory(cls, directory: Path) -> "ContextGraph":
        directory = Path(directory)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("directory must be an existing absolute directory")
        rows: list[list[dict[str, Any]]] = []
        all_ids: set[str] = set()
        for name in ("corpus.jsonl", "notes.jsonl"):
            path = directory / name
            if not path.is_file():
                raise ValueError(f"missing required {name}")
            loaded: list[dict[str, Any]] = []
            seen: set[str] = set()
            with path.open(encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{name}:{line_no}: invalid JSON") from exc
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or not isinstance(row.get("text"), str) or not row["text"]:
                        raise ValueError(f"{name}:{line_no}: each record needs string id and text")
                    if row["id"] in seen or row["id"] in all_ids:
                        raise ValueError(f"{name}:{line_no}: duplicate id")
                    if _MEDIA.search(row["text"]):
                        raise ValueError(f"{name}:{line_no}: media/base64 text rejected")
                    seen.add(row["id"])
                    all_ids.add(row["id"])
                    loaded.append(row)
            rows.append(loaded)
        return cls(rows[0], rows[1])

    def _add_node(self, node: dict[str, Any]) -> None:
        self.nodes.setdefault(node["id"], node)
        self._node_by_id.setdefault(node["id"], node)

    def _add_edge(self, edge: dict[str, Any]) -> None:
        if edge["source"] not in self.nodes or edge["target"] not in self.nodes:
            return
        if any(e["id"] == edge["id"] for e in self.edges):
            return
        self.edges.append(edge)
        self._adj[edge["source"]].append(edge)
        self._incoming[edge["target"]].append(edge)

    def _build(self) -> None:
        if tiktoken is None:
            raise ValueError("tiktoken is required for exact token budgets")
        enc = tiktoken.get_encoding("o200k_base")
        source_rows = [("Message", r) for r in self.records] + [("Memory", r) for r in self.notes]
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for kind, row in source_rows:
            text = redact(row["text"]).strip()
            if not text:
                continue
            source_id = row["id"]
            units = enc.encode(text) if enc else text.split()
            chunks = [units[i:i + 250] for i in range(0, len(units), 250)] or [[]]
            for ordinal, unit in enumerate(chunks):
                excerpt = enc.decode(unit) if enc else " ".join(unit)
                cid = _id(source_id, ordinal)
                agent = str(row.get("agent", "unknown"))
                session = str(row.get("session", "unknown"))
                role = str(row.get("role", ""))
                tag = "user_request" if role.lower() == "user" else ("reported" if role.lower() in {"assistant", "artifact"} else role)
                card_kind = "memory" if kind == "Memory" else ("task" if role.lower() in {"agent_task", "task"} else "message")
                card = {"id": cid, "source_id": source_id, "kind": card_kind, "agent": agent, "session": session,
                        "role": role, "tag": tag, "timestamp": row.get("timestamp"), "source_path": row.get("source_path"),
                        "source_line": row.get("source_line"), "source_step": row.get("source_step"),
                        "title": row.get("title"), "context": row.get("context"), "status": row.get("status"), "memory_type": row.get("memory_type"),
                        "to": row.get("to"), "handoff_status": row.get("handoff_status"), "topics": [str(t).strip()[:80] for t in (row.get("topics") or []) if str(t).strip()][:5], "text": excerpt}
                if re.search(r"\b(supersedes|replaces|superseded by)\b", excerpt, re.I): card["supersedes_mentioned"] = True
                self.cards.append(card); self._by_id[cid] = card
                self._order[cid] = len(self.cards) - 1
                grouped[(agent, session)].append(card)
                self._term_counts[cid] = Counter(_tokens(excerpt + " " + str(card.get("context") or "")))
                self._lengths[cid] = sum(self._term_counts[cid].values()) or 1
                for term in self._term_counts[cid]:
                    self._postings[term].add(cid)
                self._add_node({"id": cid, "type": "Memory" if card_kind == "memory" else "Message", **{k: v for k, v in card.items() if k != "text"}})
        agents = {c["agent"] for c in self.cards}
        for agent in sorted(agents):
            aid = f"agent:{agent}"; self._add_node({"id": aid, "type": "Agent", "agent": agent})
        for (agent, session), cards in sorted(grouped.items()):
            sid = f"session:{agent}:{session}"; self._add_node({"id": sid, "type": "Session", "agent": agent, "session": session})
            self._add_edge(_edge("HAS_SESSION", f"agent:{agent}", sid))
            for card in cards: self._add_edge(_edge("HAS_MEMORY" if card["kind"] == "memory" else "HAS_MESSAGE", sid, card["id"]))
            ordered = sorted(cards, key=lambda c: _pos(c, self._order[c["id"]]))
            for left, right in zip(ordered, ordered[1:]):
                if left["kind"] == right["kind"] == "message" and left["agent"] not in {"", "unknown"} and left["session"] not in {"", "unknown"}:
                    self._add_edge(_edge("NEXT", left["id"], right["id"]))
                elif left["kind"] == right["kind"] == "memory":
                    same_source = left["source_id"] == right["source_id"]
                    path = left.get("source_path") and left.get("source_path") == right.get("source_path")
                    numeric = all(isinstance(c.get("source_line", c.get("source_step")), int) for c in (left, right))
                    if same_source or (path and numeric): self._add_edge(_edge("NEXT", left["id"], right["id"]))
        self._topics()

    def _topics(self) -> None:
        heading_by_source: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        for row in list(self.records) + list(self.notes):
            path = str(row.get("source_path", row.get("id", "")))
            for match in _HEADING.finditer(row["text"]):
                heading = match.group(2).strip()
                tid = "topic:heading:" + hashlib.sha256(f"{path}\0{heading}".encode()).hexdigest()[:12]
                self._add_node({"id": tid, "type": "Topic", "title": heading, "source_path": path})
                heading_by_source[path].append((_pos(row, 0)[1] + row["text"][:match.start()].count("\n"), heading, tid))
        for card in self.cards:
            path = str(card.get("source_path", "")); line = _pos(card, 0)[1]
            choices = [x for x in heading_by_source.get(path, []) if x[0] <= line]
            if choices:
                _, heading, tid = choices[-1]
                card["context"] = card.get("context") or heading
                self._add_edge(_edge("HAS_SECTION", card["id"], tid))
            for label in card.get("topics", []):
                normalized = " ".join(label.casefold().split())
                tid = "topic:explicit:" + hashlib.sha256(normalized.encode()).hexdigest()[:12]
                self._add_node({"id": tid, "type": "Topic", "title": label, "explicit": True})
                self._add_edge(_edge("HAS_TOPIC", card["id"], tid))
        for card in self.cards:
            self._term_counts[card["id"]] = Counter(_tokens(card["text"] + " " + str(card.get("context") or "")))
            self._lengths[card["id"]] = sum(self._term_counts[card["id"]].values()) or 1
            for term in self._term_counts[card["id"]]:
                self._postings[term].add(card["id"])
        self._avglen = sum(self._lengths.values()) / max(1, len(self.cards))
        self._vocabulary = tuple(sorted(self._postings))

    def _query_terms(self, query: str) -> list[str]:
        terms: list[str] = []
        for term in _tokens(query):
            if term in _QUERY_STOP: continue
            terms.append(term)
            if "-" in term or "_" in term:
                terms.extend(part for part in re.split(r"[-_]", term) if part and part not in _STOP)
        return terms

    @staticmethod
    def _trigram_dice(left: str, right: str) -> float:
        grams = lambda value: {value[i:i + 3] for i in range(max(0, len(value) - 2))}
        a, b = grams(left), grams(right)
        return (2 * len(a & b)) / (len(a) + len(b)) if a and b else 0.0

    def _expanded_query(self, query: str) -> Counter[str]:
        weighted: Counter[str] = Counter()
        for term in self._query_terms(query):
            weighted[term] += 1.0
            if len(term) < 6: continue
            expansions = self._expansion_cache.get(term)
            if expansions is None:
                expansions = tuple(sorted((v for v in self._vocabulary if v != term and v.startswith(term[:5]) and self._trigram_dice(term, v) >= .5), key=lambda v: (-self._trigram_dice(term, v), v))[:2])
                self._expansion_cache[term] = expansions
            for expansion in expansions[:2]: weighted[expansion] += .35
        return weighted

    def _rank(self, query: str, augmented: bool = False) -> list[tuple[float, dict[str, Any]]]:
        q = self._expanded_query(query) if augmented else Counter(_tokens(query)); original = set(self._query_terms(query)) if augmented else set(q); n = len(self.cards); avg = self._avglen
        out = []
        for card in self.cards:
            counts = self._term_counts[card["id"]]
            score = 0.0
            for term, qtf in q.items():
                if term not in counts: continue
                df = len(self._postings.get(term, ())); idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                tf = counts[term]; contribution = idf * ((tf * 2.2) / (tf + 1.2 * (0.55 + 0.45 * self._lengths[card["id"]] / max(avg, 1)))) * (1 + min(qtf, 3) * .1)
                if augmented and term not in original: contribution *= qtf
                score += contribution
                if term in _tokens(str(card.get("context") or "")): score += .35 * (qtf if augmented and term not in original else 1)
            if score: out.append((score, card))
        return sorted(out, key=lambda x: (-x[0], x[1]["id"]))

    def _latest(self, agent: str | None = None, session: str | None = None) -> list[dict[str, Any]]:
        cards = [c for c in self.cards if c["kind"] == "message" and c["role"].lower() in {"user", "assistant"}
                 and (agent is None or c["agent"].casefold() == agent.casefold()) and (session is None or c["session"] == session)]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for card in cards: groups[(card["agent"], card["session"])].append(card)
        if not groups: return []
        def gkey(items: list[dict[str, Any]]) -> tuple[int, Any, tuple[int, int]]:
            stamps = [_stamp(c.get("timestamp")) for c in items]; known = [s for s in stamps if s]
            return (1 if known else 0, max(known) if known else datetime.min, max((_pos(c, i) for i, c in enumerate(items))))
        group = max(groups.values(), key=gkey); ordered = sorted(group, key=lambda c: _pos(c, self._order[c["id"]]))
        if not any(_stamp(c.get("timestamp")) for c in cards) and len(groups) > 1:
            return []
        result: list[dict[str, Any]] = []
        for tag in ("user_request", "reported"):
            match = next((c for c in reversed(ordered) if c["tag"] == tag), None)
            if match:
                result.extend(c for c in ordered if c["source_id"] == match["source_id"])
        return result

    def _line(self, card: dict[str, Any], absolute: bool = False) -> str:
        source = str(card.get("source_path") or "source") if absolute else Path(str(card.get("source_path") or "source")).name
        loc = source + (f":{card['source_line']}" if card.get("source_line") is not None else (f":step {card['source_step']}" if card.get("source_step") is not None else ""))
        when = card.get("timestamp") or "time unknown"
        state = f"; {card['status']}" if card.get("status") else ""
        owner = f"agent={card['agent']}" if card.get("kind") == "message" else f"agent={card['agent']}; {card.get('memory_type') or 'memory'}"
        handoff = f"; to={card['to']}; handoff={card['handoff_status']}" if card.get("to") or card.get("handoff_status") else ""
        return f"[{card['id']}] {owner}; {card['tag']} ({loc}; {when}{state}{handoff}): {card['text']}"

    def _pack(self, selected: list[dict[str, Any]], budget: int) -> tuple[str, list[dict[str, Any]]]:
        kept: list[dict[str, Any]] = []
        rendered = {c["id"]: self._line(c) for c in selected}
        costs = {cid: self._count(text) for cid, text in rendered.items()}
        used = 0
        for index, card in enumerate(selected):
            if kept and used + costs[card["id"]] + 2 > budget:
                remaining = min((costs[c["id"]] for c in selected[index:]), default=10**9)
                if used + remaining + 2 > budget: break
            candidate = "\n\n".join(self._line(c) for c in kept + [card])
            if self._count(candidate) <= budget:
                kept.append(card); used = self._count(candidate)
        return "\n\n".join(self._line(c) for c in kept), kept

    def _count(self, text: str) -> int:
        if tiktoken is None: raise ValueError("tiktoken is required for exact token budgets")
        return len(tiktoken.get_encoding("o200k_base").encode(text))

    def search(self, query: str, budget_tokens: int = 1200, mode: str = "hybrid") -> dict[str, Any]:
        if not isinstance(query, str) or not 1 <= len(query) <= 4000: raise ValueError("query length must be 1..4000")
        if isinstance(budget_tokens, bool) or not isinstance(budget_tokens, int) or not 128 <= budget_tokens <= 4000: raise ValueError("budget_tokens must be 128..4000")
        if mode not in {"flat", "hybrid", "diverse", "recency"}: raise ValueError("unknown mode")
        use_augmented = mode in {"hybrid", "diverse"}
        ranked_pairs = self._rank(query, augmented=use_augmented)
        if use_augmented:
            ranked_pairs = sorted(((score * (1.25 if card["kind"] == "memory" else .5 if card["kind"] == "task" else 1.0), card) for score, card in ranked_pairs), key=lambda item: (-item[0], item[1]["id"]))
        ranked = [c for _, c in ranked_pairs]; selected = ranked[:]; paths: dict[str, list[str]] = {}
        if mode in {"hybrid", "diverse", "recency"}:
            focus = next((a for alias, a in _AGENT_ALIASES.items() if re.search(rf"\b{re.escape(alias)}\b", query, re.I)), None)
            if _LATEST.search(query):
                if focus:
                    seed = next((c for c in ranked if c["agent"].casefold() == focus.casefold()), None)
                    anchors = self._latest(focus, seed["session"] if seed else None)
                elif ranked:
                    seed = ranked[0]
                    anchors = self._latest(seed["agent"], seed["session"])
                else:
                    anchors = []
            else:
                anchors = []
            anchors = anchors[:2]
            anchor_ids = {c["id"] for c in anchors}
            seeds = list(anchors)
            seed_sources: set[str] = set()
            for card in ranked:
                if card["id"] in anchor_ids or card["source_id"] in seed_sources: continue
                seed_sources.add(card["source_id"]); seeds.append(card)
                if len(seeds) >= 4: break
            score_by_id = {card["id"]: score for score, card in ranked_pairs}
            selected = []; seen: set[str] = set(); queue: list[tuple[dict[str, Any], list[str]]] = [(c, []) for c in seeds]
            while queue and len(selected) < max(12, len(seeds) * 3):
                queue.sort(key=lambda item: (-score_by_id.get(item[0]["id"], 0.0), item[0]["id"]))
                card, path = queue.pop(0)
                if card["id"] in seen: continue
                seen.add(card["id"]); selected.append(card); paths[card["id"]] = path
                for edge in self._adj.get(card["id"], []) + self._incoming.get(card["id"], []):
                    if edge["type"] in {"HAS_TOPIC", "HAS_SECTION"} and edge["source"] == card["id"]:
                        topic_id = edge["target"]
                        related_seen: set[str] = set()
                        for related_edge in self._incoming.get(topic_id, []):
                            if related_edge["source"] == card["id"]: continue
                            related = self._by_id.get(related_edge["source"])
                            if related and related["id"] not in seen and related["id"] not in related_seen and len(related_seen) < 2:
                                related_seen.add(related["id"])
                                queue.append((related, path + [edge["id"], related_edge["id"]]))
                        continue
                    if edge["type"] != "NEXT": continue
                    neighbor_id = edge["target"] if edge["source"] == card["id"] else edge["source"]
                    node = self._node_by_id.get(neighbor_id)
                    neighbor = self._by_id.get(neighbor_id)
                    if node and neighbor and node.get("type") in {"Message", "Memory"} and neighbor_id not in seen:
                        queue.append((neighbor, path + [edge["id"]]))
            selected += [c for c in ranked if c["id"] not in seen]
        if mode == "diverse":
            seen_sources: set[str] = set(); filtered = []
            for card in selected:
                key = (card.get("source_path"), " ".join(_tokens(card["text"])[:12]))
                if key in seen_sources and len(filtered) >= 2: continue
                seen_sources.add(key); filtered.append(card)
            selected = filtered
        if mode == "recency": selected.sort(key=lambda c: (0 if _stamp(c.get("timestamp")) else 1, -(_stamp(c.get("timestamp")) or datetime.min).timestamp() if _stamp(c.get("timestamp")) else 0))
        context, kept = self._pack(selected, budget_tokens)
        edge_ids = sorted({edge_id for c in kept for edge_id in paths.get(c["id"], [])}) if mode != "flat" else []
        return {"query": query, "context": context, "tokens": self._count(context), "cards": [{k: c.get(k) for k in ("id", "source_id", "kind", "agent", "session", "role", "tag", "status", "memory_type", "to", "handoff_status", "source_line", "source_step", "timestamp", "text")} for c in kept], "provenance": {c["id"]: {"source_id": c["source_id"], "source_path": c.get("source_path"), "source_line": c.get("source_line"), "source_step": c.get("source_step"), "timestamp": c.get("timestamp") or "time unknown"} for c in kept}, "traversed_edges": edge_ids, "overflow": len(kept) < len(selected), "omissions": [c["id"] for c in selected if c not in kept]}

    def open(self, node_id: str, budget_tokens: int = 1200, offset: int = 0) -> dict[str, Any]:
        if isinstance(budget_tokens, bool) or not isinstance(budget_tokens, int) or not 128 <= budget_tokens <= 4000: raise ValueError("budget_tokens must be 128..4000")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0: raise ValueError("offset must be a nonnegative integer")
        card = self._by_id.get(node_id)
        if not card: raise ValueError("unknown node id")
        text = card["text"][offset:]
        def render(value: str) -> str:
            full = self._line({**card, "text": value}, absolute=True)
            return full if self._count(full) <= budget_tokens else f"[{card['id']}] {value}"
        line = render(text); truncated = False
        if self._count(line) > budget_tokens:
            enc = tiktoken.get_encoding("o200k_base")
            units = enc.encode(text); lo, hi = 0, len(units)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._count(render(enc.decode(units[:mid]))) <= budget_tokens: lo = mid
                else: hi = mid - 1
            text = enc.decode(units[:lo]); line = render(text); truncated = lo < len(units)
        neighbors = [{"id": (e["target"] if e["source"] == node_id else e["source"]), "type": e["type"], "edge_id": e["id"]} for e in self._adj[node_id] + self._incoming[node_id]][:12]
        return {"node_id": node_id, "context": line, "tokens": self._count(line), "provenance": {"source_id": card["source_id"], "source_path": card.get("source_path"), "source_line": card.get("source_line"), "source_step": card.get("source_step")}, "neighbors": neighbors, "truncated": truncated, "continuation_offset": offset + len(text) if truncated else None}


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--directory", type=Path, required=True); ap.add_argument("--query", required=True); ap.add_argument("--budget", type=int, default=1200); ap.add_argument("--mode", default="hybrid", choices=("flat", "hybrid", "diverse", "recency")); ap.add_argument("--open")
    args = ap.parse_args(); graph = ContextGraph.from_directory(args.directory.resolve()); result = graph.open(args.open, args.budget) if args.open else graph.search(args.query, args.budget, args.mode)
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
