"""FalkorDB access: one graph per agent (chat + thinking) and one common graph (shared memory)
per workspace. All Cypher in the project lives in this file."""
import re
import time
import uuid
import hashlib
import threading

from falkordb import FalkorDB

from .config import settings
from .redact import redact

AGENT_RE = re.compile(r"^[a-z0-9-]{1,32}$")
KINDS = ("finding", "decision", "fact", "howto", "handoff", "rejected")
KNOWN_AGENTS = ("claude-code", "codex", "gemini", "antigravity")
FIDELITY = {"claude-code": "full", "codex": "summary", "gemini": "summary", "antigravity": "summary"}
MAX_TEXT = 4000
WORD = re.compile(r"[A-Za-z0-9_]{2,}")
_MEDIA_RE = re.compile(r"data:(?:image|audio|video)/|base64,[A-Za-z0-9+/]{80,}", re.I)


def now_ms() -> int:
    return int(time.time() * 1000)


def new_memory_id() -> str:
    return "m_" + uuid.uuid4().hex[:12]


def deterministic_memory_id(text: str) -> str:
    normalized = " ".join(text.split()).casefold()
    return "m_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def short_of(text: str, n: int = 28) -> str:
    words = text.split()
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > n:
            break
        out = (out + " " + w).strip()
    return out or text[:n]


def ft_query(q: str) -> str:
    """User text → RediSearch full-text query: any of the words, prefix-matched."""
    words = [w.lower() for w in WORD.findall(q or "")][:8]
    return "|".join(w + "*" for w in words)


def context_timestamp(value):
    if value is None: return None
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _context_position(row: dict) -> tuple[int, int]:
    for key in ("source_line", "source_step"):
        try: return (0, int(row[key]))
        except (KeyError, TypeError, ValueError): pass
    return (1, 0)


class Store:
    def __init__(self, s=settings):
        self.s = s
        self.ws = s.workspace
        if not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$", self.ws) or "__" in self.ws:
            raise ValueError("bad workspace id")
        self._db = None
        self._ready: set[str] = set()
        self._revision_lock = threading.RLock()

    @property
    def db(self):
        """Connect on first use (the client contacts the server as soon as it is built), retry after a failure."""
        if self._db is None:
            s = self.s
            self._db = FalkorDB(host=s.falkor_host, port=s.falkor_port, username=s.falkor_username,
                                password=s.falkor_password, ssl=s.falkor_ssl, socket_timeout=20)
        return self._db

    # ---------- graphs ----------
    def gname(self, agent: str | None = None) -> str:
        return f"{self.ws}__common" if agent is None else f"{self.ws}__agent__{agent}"

    def graph(self, agent: str | None = None):
        if agent is not None and not AGENT_RE.match(agent):
            raise ValueError(f"bad agent id: {agent!r}")
        name = self.gname(agent)
        g = self.db.select_graph(name)
        if name not in self._ready:
            self._schema(g, common=agent is None)
            self._ready.add(name)
        return g

    def _schema(self, g, common: bool) -> None:
        ranges = [("Agent", "id"), ("Memory", "id"), ("Concept", "key"), ("Room", "id"), ("Turn", "id"), ("WorkspaceState", "id")] if common \
            else [("Session", "id"), ("Message", "id"), ("Thought", "id"), ("MemoryRef", "memory_id")]
        texts = [("Memory", "text")] if common else [("Message", "text"), ("Thought", "text")]
        for label, prop in ranges:
            self._idx(lambda: g.create_node_range_index(label, prop))
        for label, prop in texts:
            self._idx(lambda: g.create_node_fulltext_index(label, prop))

    @staticmethod
    def _idx(fn) -> None:
        try:
            fn()
        except Exception as e:  # index already exists
            if "already" not in str(e).lower():
                raise

    def agents(self) -> list[str]:
        prefix = f"{self.ws}__agent__"
        found = [n[len(prefix):] for n in self.db.list_graphs() if n.startswith(prefix)]
        return sorted({a for a in set(found) | set(KNOWN_AGENTS) if AGENT_RE.match(a)}, key=lambda a: (a not in KNOWN_AGENTS, a))

    def ping(self) -> bool:
        self.db.list_graphs()
        return True

    # ---------- persistent context export/import ----------
    def context_revision(self) -> int:
        row = self.graph().query("MATCH (s:WorkspaceState {id: 'context'}) RETURN coalesce(s.revision, 0)").result_set
        return int(row[0][0]) if row else 0

    def _touch_context(self) -> int:
        with self._revision_lock:
            row = self.graph().query("MERGE (s:WorkspaceState {id: 'context'}) SET s.revision = coalesce(s.revision, 0) + 1 RETURN s.revision").result_set
            return int(row[0][0])

    def context_snapshot(self) -> dict:
        before = self.context_revision()
        records: list[dict] = []
        graph_names = set(self.db.list_graphs())
        for agent in self.agents():
            name = self.gname(agent)
            if name not in graph_names: continue
            g = self.db.select_graph(name)
            rows = g.query("""MATCH (m:Message) RETURN m.id, m.role, m.text, m.session, m.ts, m.turn,
                m.source_id, m.source_path, m.source_line, m.source_step, m.agent ORDER BY m.ts, m.id""").result_set
            for r in rows:
                item = dict(zip(("id", "role", "text", "session", "timestamp", "turn", "source_id", "source_path", "source_line", "source_step", "agent"), r))
                item["source_id"] = item.get("source_id") or item["id"]
                item["agent"] = item.get("agent") or agent
                item["timestamp"] = context_timestamp(item.get("timestamp"))
                records.append(item)
        notes: list[dict] = []
        rows = self.graph().query("""MATCH (m:Memory) OPTIONAL MATCH (m)-[:ABOUT]->(k:Concept)
            RETURN coalesce(m.source_id, m.id), m.id, m.kind, m.memory_type, m.text, m.agent, m.status, m.to, m.handoff_status, m.ts, m.source_path,
            m.source_line, m.source_step, collect(k.name)""").result_set
        for r in rows:
            notes.append(dict(zip(("id", "memory_id", "kind", "memory_type", "text", "agent", "status", "to", "handoff_status", "timestamp", "source_path", "source_line", "source_step", "topics"), r)))
            notes[-1]["source_id"] = notes[-1]["id"]
            notes[-1]["timestamp"] = context_timestamp(notes[-1].get("timestamp"))
        after = self.context_revision()
        return {"revision": after, "records": records, "notes": notes, "consistent": before == after}

    def import_context(self, records: list[dict], notes: list[dict]) -> dict:
        if not isinstance(records, list) or not isinstance(notes, list): raise ValueError("records and notes must be lists")
        all_ids: set[str] = set()
        for row in records + notes:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or not isinstance(row.get("text"), str) or not row["text"]:
                raise ValueError("context rows need nonempty id and text")
            if row["id"] in all_ids: raise ValueError("duplicate context id")
            all_ids.add(row["id"])
            if _MEDIA_RE.search(row["text"]): raise ValueError("media/base64 context rejected")
        imported_messages = 0
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in records:
            agent = str(row.get("agent", "unknown")); session = str(row.get("session", "unknown"))
            if agent == "claude": agent = "claude-code"
            if not AGENT_RE.match(agent): raise ValueError("bad agent id")
            grouped.setdefault((agent, session), []).append(row)
        for (agent, session), rows in grouped.items():
            g = self.graph(agent)
            ordered = sorted(rows, key=lambda r: (_context_position(r), r["id"]))
            current_turn = None; inferred_turns: dict[str, str] = {}
            for row in ordered:
                explicit = row.get("turn")
                if isinstance(explicit, str) and explicit.strip(): current_turn = explicit
                elif str(row.get("role", "")).lower() == "user": current_turn = row["id"]
                elif current_turn is None: current_turn = f"{session}:preamble:{row['id']}"
                inferred_turns[row["id"]] = current_turn
            user = next((r for r in ordered if str(r.get("role", "")).lower() == "user"), None)
            title = redact(str(user.get("text", ""))[:120]) if user else session
            starts = [r.get("timestamp") for r in ordered if r.get("timestamp") and str(r.get("timestamp")).lower() not in {"time unknown", "unknown"}]
            g.query("MERGE (s:Session {id: $id}) SET s.agent=$agent, s.started=coalesce(s.started,$started), s.title=CASE WHEN s.title IS NULL OR s.title='' THEN $title ELSE s.title END", {"id": session, "agent": agent, "title": title, "started": min(starts) if starts else None})
            payload = [{"id": r["id"], "role": r.get("role", ""), "text": redact(r["text"]), "session": session,
                        "ts": r.get("timestamp"), "turn": inferred_turns[r["id"]], "source_id": r.get("source_id", r["id"]),
                        "source_path": r.get("source_path"), "source_line": r.get("source_line"), "source_step": r.get("source_step")} for r in ordered]
            g.query("""UNWIND $rows AS r MERGE (m:Message {id:r.id}) SET m.role=r.role,m.text=r.text,m.session=r.session,m.ts=r.ts,m.turn=r.turn,
                m.source_id=r.source_id,m.source_path=r.source_path,m.source_line=r.source_line,m.source_step=r.source_step,m.agent=$agent
                WITH m,r MATCH (s:Session {id:r.session}) MERGE (s)-[:HAS]->(m)""", {"rows": payload, "agent": agent})
            links = [{"a": left["id"], "b": right["id"]} for left, right in zip(ordered, ordered[1:])]
            if links:
                g.query("UNWIND $rows AS r MATCH (a:Message {id:r.a}),(b:Message {id:r.b}) MERGE (a)-[:NEXT]->(b)", {"rows": links})
            imported_messages += len(rows)
        imported_notes = 0
        note_rows = []
        for row in notes:
            mid = "m_" + hashlib.sha256(row["id"].encode()).hexdigest()[:12]
            explicit = str(row.get("kind", "")).lower()
            procedural = str(row.get("memory_type", "")).lower() == "procedural" or explicit in {"howto", "procedure"} or "best_way" in str(row.get("source_path", "")).lower()
            kind = explicit if explicit in KINDS else ("howto" if procedural else "fact")
            agent = str(row.get("agent", "artifact")); agent = "artifact" if not AGENT_RE.match(agent) else agent
            note_rows.append({"id": mid, "kind": kind, "memory_type": row.get("memory_type", "procedural" if kind == "howto" else "semantic"), "text": redact(row["text"]), "agent": agent,
                "status": row.get("status", "active"), "handoff_status": row.get("handoff_status"), "ts": row.get("timestamp"), "source_id": row.get("source_id", row["id"]),
                "source_path": row.get("source_path"), "source_line": row.get("source_line"), "source_step": row.get("source_step"), "topics": [str(t)[:80] for t in (row.get("topics") or [])[:8]]})
        c = self.graph()
        for start in range(0, len(note_rows), 100):
            batch = note_rows[start:start + 100]
            c.query("""UNWIND $rows AS r MERGE (a:Agent {id:r.agent}) MERGE (m:Memory {id:r.id})
                ON CREATE SET m.kind=r.kind,m.memory_type=r.memory_type,m.text=r.text,m.agent=r.agent,m.status=r.status,m.handoff_status=r.handoff_status,m.ts=r.ts,
                    m.source_id=r.source_id,m.source_path=r.source_path,m.source_line=r.source_line,m.source_step=r.source_step
                MERGE (a)-[:WROTE]->(m)""", {"rows": batch})
            topic_rows = [{"id": r["id"], "topic": t} for r in batch for t in r["topics"]]
            if topic_rows:
                c.query("""UNWIND $rows AS r MATCH (m:Memory {id:r.id}) MERGE (k:Concept {key:toLower(r.topic)}) ON CREATE SET k.name=r.topic MERGE (m)-[:ABOUT]->(k)""", {"rows": topic_rows})
            imported_notes += len(batch)
        if imported_messages or imported_notes: self._touch_context()
        return {"records": imported_messages, "notes": imported_notes, "revision": self.context_revision()}

    # ---------- capture: agent graphs ----------
    def ingest(self, agent: str, events: list[dict]) -> dict:
        """Write parser events (session / message / thought / recall) into the agent's graph."""
        g = self.graph(agent)
        sessions = [e for e in events if e["kind"] == "session"]
        messages = [e for e in events if e["kind"] == "message"]
        thoughts = [e for e in events if e["kind"] == "thought"]
        recalls = [e for e in events if e["kind"] == "recall"]
        if sessions:
            g.query("""UNWIND $rows AS r
                MERGE (s:Session {id: r.id})
                SET s.agent = $agent, s.cwd = coalesce(r.cwd, s.cwd), s.started = coalesce(s.started, r.ts),
                    s.title = CASE WHEN r.title IS NULL OR r.title = '' THEN s.title ELSE r.title END""",
                    {"rows": [{"id": e["id"], "cwd": e.get("cwd"), "ts": e.get("ts"), "title": redact(e.get("title"))[:80]}
                              for e in sessions], "agent": agent})
        if messages:
            g.query("""UNWIND $rows AS r
                MERGE (m:Message {id: r.id})
                SET m.role = r.role, m.text = r.text, m.ts = r.ts, m.session = r.session, m.turn = r.turn,
                    m.extracted = coalesce(m.extracted, false)
                WITH m, r WHERE r.role = 'user' OR r.prev IS NULL
                MATCH (s:Session {id: r.session}) MERGE (s)-[:HAS]->(m)""",
                    {"rows": [{"id": e["id"], "role": e["role"], "text": redact(e["text"])[:MAX_TEXT], "ts": e.get("ts"),
                               "session": e["session"], "turn": e.get("turn"), "prev": e.get("prev")} for e in messages]})
        if thoughts:
            g.query("""UNWIND $rows AS r
                MERGE (t:Thought {id: r.id})
                SET t.text = r.text, t.fidelity = r.fidelity, t.ts = r.ts, t.session = r.session, t.turn = r.turn,
                    t.extracted = coalesce(t.extracted, false)""",
                    {"rows": [{"id": e["id"], "text": redact(e["text"])[:MAX_TEXT], "fidelity": e.get("fidelity", "full"),
                               "ts": e.get("ts"), "session": e["session"], "turn": e.get("turn")} for e in thoughts]})
        self._chain(g, messages + thoughts)
        for r in recalls:
            self.link_recall(agent, r.get("memory_ids", []), r.get("thought"))
        if sessions or messages or thoughts or recalls: self._touch_context()
        return {"sessions": len(sessions), "messages": len(messages), "thoughts": len(thoughts), "recalls": len(recalls)}

    @staticmethod
    def _chain(g, items: list[dict]) -> None:
        """Link each item to the one before it in its turn: your message → thought → … → reply."""
        label = lambda e: "Thought" if e["kind"] == "thought" else "Message"
        groups: dict[tuple, list] = {}
        for e in items:
            if e.get("prev"):
                groups.setdefault((e.get("prev_label", "Message"), label(e)), []).append({"a": e["prev"], "b": e["id"]})
        rel = {("Message", "Thought"): "THOUGHT", ("Thought", "Thought"): "NEXT",
               ("Thought", "Message"): "LED_TO", ("Message", "Message"): "NEXT"}
        for (la, lb), rows in groups.items():
            g.query(f"UNWIND $rows AS r MATCH (a:{la} {{id: r.a}}), (b:{lb} {{id: r.b}}) MERGE (a)-[:{rel[(la, lb)]}]->(b)",
                    {"rows": rows})

    # ---------- shared memory: common graph ----------
    def remember(self, agent: str, kind: str, text: str, topics: list[str] | None = None, short: str | None = None,
                 src: str | None = None, supersedes: str | None = None, to: str | None = None, mem_id: str | None = None) -> str:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if not AGENT_RE.match(agent) or (to and not AGENT_RE.match(to)):
            raise ValueError("bad agent id")
        text = redact(text).strip()[:1000]
        if not text:
            raise ValueError("empty memory")
        c = self.graph()
        dup = c.query("MATCH (m:Memory {text: $t}) RETURN m.id LIMIT 1", {"t": text}).result_set
        if dup:
            return dup[0][0]
        mid = mem_id or deterministic_memory_id(text)
        ts = now_ms()
        memory_type = "procedural" if kind == "howto" else ("coordination" if kind == "handoff" else "semantic")
        c.query("""MERGE (a:Agent {id: $agent})
            MERGE (m:Memory {id: $id})
            ON CREATE SET m.kind = $kind, m.text = $text, m.short = $short, m.agent = $agent, m.status = 'active',
                m.ts = $ts, m.created_ts = $ts, m.src_graph = $src_graph, m.src_id = $src, m.to = $to,
                m.memory_type = $memory_type, m.handoff_status = CASE WHEN $kind = 'handoff' THEN 'open' ELSE null END
            MERGE (a)-[:WROTE]->(m)""",
                {"agent": agent, "id": mid, "kind": kind, "text": text, "short": redact(short or short_of(text))[:40],
                 "ts": ts, "src_graph": self.gname(agent) if src else None, "src": src, "to": to, "memory_type": memory_type})
        topics = [t.strip()[:40] for t in (topics or []) if t and t.strip()][:5]
        if topics:
            c.query("""MATCH (m:Memory {id: $id}), (a:Agent {id: $agent})
                UNWIND $topics AS t
                MERGE (k:Concept {key: toLower(t)}) ON CREATE SET k.name = t
                MERGE (m)-[:ABOUT]->(k)
                MERGE (a)-[w:KNOWS]->(k) ON CREATE SET w.count = 1 ON MATCH SET w.count = w.count + 1""",
                    {"id": mid, "agent": agent, "topics": topics})
        if supersedes:
            c.query("""MATCH (m:Memory {id: $id}), (o:Memory {id: $old}) WHERE o.id <> m.id
                SET o.status = 'superseded' MERGE (m)-[:SUPERSEDES]->(o)""", {"id": mid, "old": supersedes})
        if to:
            c.query("MATCH (m:Memory {id: $id}) MERGE (b:Agent {id: $to}) MERGE (m)-[:TO]->(b)", {"id": mid, "to": to})
        if src:  # the reply or thought this memory came from, in the writer's own graph
            g = self.graph(agent)
            for label in ("Message", "Thought"):
                g.query(f"""MATCH (x:{label} {{id: $src}})
                    MERGE (r:MemoryRef {{memory_id: $id}}) SET r.dir = 'out', r.short = $short
                    MERGE (x)-[:SAVED]->(r)""", {"src": src, "id": mid, "short": short_of(text)})
        self._touch_context()
        return mid

    def record_recall(self, reader: str, memory_ids: list[str]) -> None:
        """Server side: another agent's memory came back from this reader's recall/sync."""
        if not memory_ids:
            return
        self.graph().query("""UNWIND $ids AS mid
            MATCH (m:Memory {id: mid}) WHERE m.agent <> $reader
            MERGE (a:Agent {id: $reader})
            MERGE (m)-[r:RECALLED]->(a)
            ON CREATE SET r.count = 1, r.first = $ts, r.thoughts = []
            ON MATCH SET r.count = r.count + 1
            SET r.last = $ts""", {"ids": memory_ids, "reader": reader, "ts": now_ms()})

    def link_recall(self, reader: str, memory_ids: list[str], thought_id: str | None) -> None:
        """Collector side: tie a recall to the exact thought that made it (no count change)."""
        if not memory_ids:
            return
        g = self.graph(reader)
        g.query("""UNWIND $ids AS mid MERGE (r:MemoryRef {memory_id: mid}) ON CREATE SET r.dir = 'in'""", {"ids": memory_ids})
        if thought_id:
            g.query("""MATCH (t:Thought {id: $t}) UNWIND $ids AS mid
                MATCH (r:MemoryRef {memory_id: mid}) MERGE (t)-[:RECALLED]->(r)""", {"t": thought_id, "ids": memory_ids})
            self.graph().query("""UNWIND $ids AS mid MATCH (m:Memory {id: mid})-[r:RECALLED]->(:Agent {id: $reader})
                SET r.thoughts = CASE WHEN $t IN r.thoughts THEN r.thoughts ELSE r.thoughts + [$t] END""",
                               {"ids": memory_ids, "reader": reader, "t": thought_id})

    def recall(self, reader: str, q: str, limit: int = 5) -> dict:
        """Best matching memories (most used first), plus matching chat and thinking from other agents."""
        c = self.graph()
        query = ft_query(q)
        if query:
            rows = c.query("""CALL db.idx.fulltext.queryNodes('Memory', $q) YIELD node, score
                WHERE node.status = 'active'
                OPTIONAL MATCH (node)-[r:RECALLED]->()
                WITH node, score, count(r) AS uses
                RETURN node.id, node.kind, node.short, node.text, node.agent, uses
                ORDER BY score DESC, uses DESC LIMIT $limit""", {"q": query, "limit": limit}).result_set
        else:
            rows = c.query("""MATCH (node:Memory {status: 'active'}) OPTIONAL MATCH (node)-[r:RECALLED]->()
                WITH node, count(r) AS uses RETURN node.id, node.kind, node.short, node.text, node.agent, uses
                ORDER BY node.ts DESC LIMIT $limit""", {"limit": limit}).result_set
        memories = [dict(zip(("id", "kind", "short", "text", "agent", "uses"), r)) for r in rows]
        chat = []
        if query:
            for other in self.agents():
                if other == reader or self.gname(other) not in self.db.list_graphs():
                    continue
                g = self.graph(other)
                for label in ("Thought", "Message"):
                    hits = g.query(f"""CALL db.idx.fulltext.queryNodes('{label}', $q) YIELD node, score
                        RETURN node.id, node.text, node.session, score ORDER BY score DESC LIMIT 2""", {"q": query}).result_set
                    chat += [{"agent": other, "kind": label.lower(), "id": h[0], "text": h[1][:300], "session": h[2]} for h in hits]
        self.record_recall(reader, [m["id"] for m in memories])
        return {"memories": memories, "chat": chat[:6]}

    def sync(self, agent: str) -> dict:
        c = self.graph()
        inbox = c.query("""MATCH (a:Agent)-[:WROTE]->(m:Memory {kind: 'handoff', to: $agent})
            WHERE m.handoff_status IN ['open', 'accepted']
            RETURN m.id, a.id, m.text, m.handoff_status ORDER BY m.ts""", {"agent": agent}).result_set
        recent = c.query("""MATCH (m:Memory {status: 'active'}) WHERE m.agent <> $agent AND m.kind <> 'handoff'
            RETURN m.id, m.agent, m.kind, m.text ORDER BY m.ts DESC LIMIT 5""", {"agent": agent}).result_set
        self.record_recall(agent, [r[0] for r in recent])
        return {"handoffs": [dict(zip(("id", "from", "text", "status"), r)) for r in inbox],
                "recent_from_others": [dict(zip(("id", "agent", "kind", "text"), r)) for r in recent]}

    def pending_footer(self, agent: str) -> str:
        """Appended to every tool reply, so no agent misses a request."""
        rows = self.graph().query("""MATCH (a:Agent)-[:WROTE]->(m:Memory {kind: 'handoff', to: $agent, handoff_status: 'open'})
            RETURN m.id, a.id, m.short""", {"agent": agent}).result_set
        if not rows:
            return ""
        return "\n\n📬 Open handoffs for you: " + "; ".join(f"{r[0]} from {r[1]}: {r[2]}" for r in rows) + \
               " — answer with respond(handoff_id, accept|decline|done, note)."

    def respond(self, agent: str, handoff_id: str, decision: str, note: str = "") -> bool:
        if decision not in ("accept", "decline", "done"):
            raise ValueError("decision must be accept, decline or done")
        status = {"accept": "accepted", "decline": "declined", "done": "done"}[decision]
        res = self.graph().query("""MATCH (m:Memory {id: $id, kind: 'handoff', to: $agent})
            SET m.handoff_status = $status, m.note = $note RETURN m.id""",
                                 {"id": handoff_id, "agent": agent, "status": status, "note": redact(note)[:500]})
        changed = bool(res.result_set)
        if changed: self._touch_context()
        return changed

    def peers(self, agent: str) -> list[dict]:
        out = []
        names = set(self.db.list_graphs())
        for other in self.agents():
            if other == agent or self.gname(other) not in names:
                continue
            rows = self.graph(other).query("""MATCH (s:Session) RETURN s.id, s.title, s.started
                ORDER BY s.started DESC LIMIT 3""").result_set
            out.append({"agent": other, "recent_sessions": [{"id": r[0], "title": r[1], "started": r[2]} for r in rows]})
        return out

    def checkpoint(self, agent: str, summary: str, decisions: list[str]) -> dict:
        ts = now_ms()
        sid, mid = f"{agent}:checkpoints", f"{agent}:checkpoint:{ts}"
        self.ingest(agent, [{"kind": "session", "id": sid, "title": "checkpoints", "ts": ts},
                            {"kind": "message", "id": mid, "role": "assistant", "text": summary, "session": sid, "ts": ts}])
        ids = [self.remember(agent, "decision", d, src=mid) for d in decisions if d.strip()]
        return {"message": mid, "decisions": ids}

    def trace(self, memory_id: str) -> dict | None:
        c = self.graph()
        row = c.query("""MATCH (m:Memory {id: $id}) OPTIONAL MATCH (m)-[:ABOUT]->(k:Concept)
            RETURN m.id, m.kind, m.text, m.agent, m.src_id, m.status, collect(k.name)""", {"id": memory_id}).result_set
        if not row:
            return None
        mid, kind, text, writer, src, status, topics = row[0]
        out = {"id": mid, "kind": kind, "text": text, "agent": writer, "status": status, "topics": topics, "source": None, "recalled_by": []}
        if src:
            g = self.graph(writer)
            s = g.query("""MATCH (x {id: $src}) RETURN x.text, x.turn, x.session""", {"src": src}).result_set
            if s:
                turn = self._turn(g, s[0][1]) if s[0][1] else []
                out["source"] = {"text": s[0][0], "session": s[0][2], "turn": turn}
        for reader, thoughts, count in c.query("""MATCH (m:Memory {id: $id})-[r:RECALLED]->(a:Agent)
                RETURN a.id, r.thoughts, r.count""", {"id": memory_id}).result_set:
            texts = []
            if thoughts:
                texts = [t[0] for t in self.graph(reader).query(
                    "UNWIND $ids AS i MATCH (t:Thought {id: i}) RETURN t.text", {"ids": thoughts}).result_set]
            out["recalled_by"].append({"agent": reader, "count": count, "thoughts": texts})
        return out

    @staticmethod
    def _turn(g, turn: str) -> list[dict]:
        rows = g.query("""MATCH (x) WHERE (x:Message OR x:Thought) AND x.turn = $turn
            RETURN labels(x)[0], x.role, x.text, x.ts ORDER BY x.ts""", {"turn": turn}).result_set
        return [{"type": r[0].lower(), "role": r[1], "text": r[2]} for r in rows]

    # ---------- the LLM step ----------
    def pending_items(self, limit: int = 40) -> list[dict]:
        out = []
        names = set(self.db.list_graphs())
        for agent in self.agents():
            if self.gname(agent) not in names:
                continue
            rows = self.graph(agent).query("""MATCH (x) WHERE (x:Message OR x:Thought) AND x.extracted = false
                RETURN x.id, labels(x)[0], x.role, x.text, x.session ORDER BY x.ts LIMIT $n""", {"n": limit}).result_set
            out += [{"agent": agent, "id": r[0], "type": r[1].lower(), "role": r[2], "text": r[3], "session": r[4]} for r in rows]
        return out[:limit]

    def mark_extracted(self, agent: str, ids: list[str]) -> None:
        self.graph(agent).query("UNWIND $ids AS i MATCH (x {id: i}) SET x.extracted = true", {"ids": ids})

    # ---------- what the pages show (same shape as web/data.js) ----------
    def state(self, sessions_per_agent: int = 6, turns_per_session: int = 8) -> dict:
        c = self.graph()
        mems = {}
        for r in c.query("""MATCH (a:Agent)-[:WROTE]->(m:Memory)
                OPTIONAL MATCH (m)-[:ABOUT]->(k:Concept) OPTIONAL MATCH (m)-[:SUPERSEDES]->(o:Memory)
                RETURN m.id, a.id, m.kind, m.short, m.text, collect(DISTINCT k.name), collect(DISTINCT o.id), m.to, m.status,
                       m.handoff_status, m.ts
                ORDER BY m.ts""").result_set:
            mems[r[0]] = {"by": r[1], "kind": r[2], "short": r[3], "text": r[4], "topics": r[5] or ["general"],
                          "supersedes": (r[6] or [None])[0], "to": r[7], "status": r[8], "handoff_status": r[9], "ts": r[10]}
        names = set(self.db.list_graphs())
        logs = {}
        for agent in self.agents():
            logs[agent] = []
            if self.gname(agent) not in names:
                continue
            g = self.graph(agent)
            for sid, title in g.query("""MATCH (s:Session) RETURN s.id, s.title ORDER BY s.started DESC LIMIT $n""",
                                      {"n": sessions_per_agent}).result_set:
                rows = g.query("""MATCH (x) WHERE (x:Message OR x:Thought) AND x.session = $sid
                    OPTIONAL MATCH (x)-[rel:RECALLED|SAVED]->(r:MemoryRef)
                    WITH x, collect(r.memory_id) AS refs, coalesce(x.ts, x.source_step, x.source_line) AS sort_key
                    RETURN x.id, labels(x)[0], x.role, x.text, x.turn, x.ts, refs, sort_key
                    ORDER BY sort_key, x.id""", {"sid": sid}).result_set
                turns: dict = {}
                for xid, label, role, text, turn, ts, refs, _sort_key in rows:
                    key = turn or f"{sid}|pre"
                    t = turns.setdefault(key, {"id": key, "user": "", "think": [], "reply": "", "save": []})
                    refs = [m for m in refs if m in mems]
                    if label == "Thought":
                        t["think"].append({"t": text, "recall": refs})
                    elif role == "user":
                        t["user"] = text
                    else:
                        t["reply"] = (t["reply"] + "\n" + text).strip() if t["reply"] else text
                        t["save"] += refs
                kept = [t for t in turns.values() if t["user"] or t["reply"]][-turns_per_session:]
                if kept:
                    logs[agent].append({"id": sid, "title": title or "chat", "turns": kept})
        return {"AG": {a: {"name": AGENT_NAMES.get(a, a), "mono": AGENT_NAMES.get(a, a)[0].upper() if a != "codex" else "X"}
                       for a in self.agents()},
                "FID": {a: FIDELITY.get(a, "summary") for a in self.agents()},
                "MEMS": mems, "LOGS": logs, "SOURCE": "live"}


AGENT_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI", "antigravity": "Antigravity"}
