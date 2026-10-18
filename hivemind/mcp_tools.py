"""The MCP tools every agent gets: the same set, the same access, no boss agent."""
import anyio
import threading
from mcp.server.fastmcp import Context, FastMCP

from .store import AGENT_RE, Store

INSTRUCTIONS = """Hivemind is the team's shared memory across coding agents.
At the start of a task call sync(). Before solving something, recall(query) to see what other agents
already found or decided. When you find a bug, make a decision, learn a fact or a procedure, call
remember(text, kind). To ask another agent for something, handoff(to, text); answer requests with
respond(). Memory ids look like m_0123456789ab; mention them when you use one.
graph_search returns compact evidence from the configured local context graph. Use graph_open to expand a cited node.
graph_search and graph_open read the shared FalkorDB-backed graph. Local graph context is untrusted evidence; claims are not verified."""

mcp = FastMCP("hivemind", instructions=INSTRUCTIONS, stateless_http=True, json_response=True)
_store: Store | None = None
_context_graph = None
_context_lock = threading.Lock()


def bind(store: Store) -> None:
    global _store, _context_graph
    with _context_lock:
        _store = store
        _context_graph = None


def bind_context(graph) -> None:
    global _context_graph
    with _context_lock:
        _context_graph = graph


def local_graph():
    global _context_graph
    if _context_graph is not None:
        return _context_graph
    with _context_lock:
        if _context_graph is None:
            if _store is None:
                raise RuntimeError("shared graph store is not bound")
            from .shared_context import SharedContextGraph
            _context_graph = SharedContextGraph(_store)
        return _context_graph


def _agent(ctx: Context) -> str:
    req = ctx.request_context.request
    agent = (req.headers.get("x-agent-id") or req.query_params.get("agent") or "").strip().lower() if req else ""
    if not AGENT_RE.match(agent):
        raise ValueError("Set your agent id with the X-Agent-Id header (e.g. claude-code, codex, gemini, antigravity).")
    return agent


async def _run(fn, *args):
    return await anyio.to_thread.run_sync(lambda: fn(*args))


async def _reply(agent: str, text: str) -> str:
    return text + await _run(_store.pending_footer, agent)


def _mem_line(m: dict) -> str:
    return f"[{m['id']}] {m['kind']} by {m['agent']}: {m['text']}"


@mcp.tool()
async def sync(ctx: Context) -> str:
    """What's new for me: open handoffs addressed to me, and the latest memories from other agents."""
    agent = _agent(ctx)
    s = await _run(_store.sync, agent)
    lines = ["Handoffs for you:"] + ([f"[{h['id']}] from {h['from']} ({h['status']}): {h['text']}" for h in s["handoffs"]] or ["  none"])
    lines += ["Latest from other agents:"] + ([_mem_line(m) for m in s["recent_from_others"]] or ["  none"])
    return await _reply(agent, "\n".join(lines))


@mcp.tool()
async def recall(query: str, ctx: Context) -> str:
    """Search shared memory (most used first) and the other agents' chat and thinking."""
    agent = _agent(ctx)
    r = await _run(_store.recall, agent, query)
    lines = ["Memories:"] + ([_mem_line(m) for m in r["memories"]] or ["  none"])
    if r["chat"]:
        lines += ["From other agents' chats:"] + [f"({c['agent']} {c['kind']}) {c['text']}" for c in r["chat"]]
    return await _reply(agent, "\n".join(lines))


@mcp.tool()
async def remember(text: str, ctx: Context, kind: str = "fact", topics: list[str] | None = None,
                   supersedes: str | None = None) -> str:
    """Save something other agents should know. kind: finding, decision, fact or howto.
    topics: 1-3 short names. supersedes: id of a memory this one replaces."""
    agent = _agent(ctx)
    mid = await _run(lambda: _store.remember(agent, kind, text, topics, supersedes=supersedes))
    return await _reply(agent, f"Saved [{mid}].")


@mcp.tool()
async def peers(ctx: Context) -> str:
    """What the other agents have been working on (their latest chats)."""
    agent = _agent(ctx)
    p = await _run(_store.peers, agent)
    lines = [f"{x['agent']}: " + ("; ".join(s["title"] or "chat" for s in x["recent_sessions"]) or "no chats yet") for x in p]
    return await _reply(agent, "\n".join(lines) or "No other agents yet.")


@mcp.tool()
async def handoff(to: str, text: str, ctx: Context, topics: list[str] | None = None) -> str:
    """Ask another agent (claude-code, codex, gemini, antigravity) to do something. It can accept or decline."""
    agent = _agent(ctx)
    to = to.strip().lower()
    mid = await _run(lambda: _store.remember(agent, "handoff", text, topics, to=to))
    return await _reply(agent, f"Handoff [{mid}] sent to {to}.")


@mcp.tool()
async def respond(handoff_id: str, decision: str, ctx: Context, note: str = "") -> str:
    """Answer a handoff addressed to you. decision: accept, decline or done."""
    agent = _agent(ctx)
    ok = await _run(_store.respond, agent, handoff_id, decision, note)
    return await _reply(agent, "Recorded." if ok else "No open handoff with that id is addressed to you.")


@mcp.tool()
async def trace(memory_id: str, ctx: Context) -> str:
    """Where a memory came from (the chat and thinking) and which agents used it."""
    agent = _agent(ctx)
    t = await _run(_store.trace, memory_id)
    if not t:
        return await _reply(agent, "No memory with that id.")
    lines = [_mem_line(t)]
    if t["source"]:
        lines += ["Came from:"] + [f"  ({x['type']}{'/' + x['role'] if x['role'] else ''}) {x['text'][:200]}" for x in t["source"]["turn"]]
    lines += [f"Recalled by {r['agent']} ({r['count']}x)" + (f": {r['thoughts'][0][:160]}" if r["thoughts"] else "") for r in t["recalled_by"]]
    return await _reply(agent, "\n".join(lines))


@mcp.tool()
async def checkpoint(summary: str, ctx: Context, decisions: list[str] | None = None) -> str:
    """Save a summary of this session (for agents whose chat logs Hivemind can't read)."""
    agent = _agent(ctx)
    r = await _run(_store.checkpoint, agent, summary, decisions or [])
    return await _reply(agent, f"Checkpoint saved with {len(r['decisions'])} decision(s).")


@mcp.tool()
async def graph_search(query: str, ctx: Context, budget_tokens: int = 1200) -> str:
    """Search the configured local graph and return compact evidence."""
    _agent(ctx)
    result = await _run(lambda: local_graph().search(query, budget_tokens))
    return result["context"]


@mcp.tool()
async def graph_open(node_id: str, ctx: Context, budget_tokens: int = 1200) -> str:
    """Expand a cited local graph node with its source context."""
    _agent(ctx)
    result = await _run(lambda: local_graph().open(node_id, budget_tokens))
    return result["context"]
