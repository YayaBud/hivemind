"""Hivemind server: MCP for agents, /ingest for daemons, /api + /events for the pages, and the pages.

Run:  python -m uvicorn hivemind.server:app --host 127.0.0.1 --port 5199
"""
import asyncio
import contextlib
import hmac
import json

import anyio
from redis.exceptions import RedisError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import mcp_tools
from .config import ROOT, settings
from .store import AGENT_RE, Store

store = Store()
mcp_tools.bind(store)
mcp_app = mcp_tools.mcp.streamable_http_app()  # creates the session manager used in lifespan
_listeners: set[asyncio.Queue] = set()


@contextlib.asynccontextmanager
async def lifespan(_app):
    async with mcp_tools.mcp.session_manager.run():
        yield


app = FastAPI(title="Hivemind", lifespan=lifespan)
app.router.routes.extend(mcp_app.routes)  # POST/GET /mcp, exactly that path


def _token_ok(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    given = auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-hivemind-token", "")
    return hmac.compare_digest(given.encode(), settings.token.encode())


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    private = path.startswith("/mcp") or path.startswith("/ingest") or path == "/api/pending" or \
        (path.startswith("/api/") and request.method != "GET")
    if private and not _token_ok(request):
        return JSONResponse({"error": "missing or wrong token"}, status_code=401)
    return await call_next(request)


async def _db(fn, *args):
    try:
        return await anyio.to_thread.run_sync(lambda: fn(*args))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # database unreachable or misconfigured
        raise HTTPException(503, f"FalkorDB unavailable: {type(e).__name__}")


def _context_body(body: object, *, open_request: bool) -> tuple[str, int]:
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON body must be an object")
    field = "node_id" if open_request else "query"
    value = body.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise HTTPException(400, f"{field} must be a non-empty string of at most 4000 characters")
    budget = body.get("budget_tokens", 1200)
    if isinstance(budget, bool) or not isinstance(budget, int) or not 128 <= budget <= 4000:
        raise HTTPException(400, "budget_tokens must be an integer from 128 to 4000")
    return value, budget


async def _context_graph_call(method: str, value: str, budget: int):
    try:
        graph = await anyio.to_thread.run_sync(mcp_tools.local_graph)
        return await anyio.to_thread.run_sync(lambda: getattr(graph, method)(value, budget))
    except (OSError, RuntimeError, ImportError, RedisError, ConnectionError):
        raise HTTPException(503, "local context graph unavailable")
    except ValueError as exc:
        message = str(exc)
        if message == "unknown node id":
            raise HTTPException(404, message)
        if message == "shared graph store is not bound":
            raise HTTPException(503, "local context graph unavailable")
        raise HTTPException(400, message)


@app.post("/api/context/search")
async def context_search(request: Request):
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "invalid JSON body")
    query, budget = _context_body(body, open_request=False)
    result = await _context_graph_call("search", query, budget)
    return {"context": result["context"], "tokens": result["tokens"], "node_ids": [card["id"] for card in result.get("cards", [])]}


@app.post("/api/context/open")
async def context_open(request: Request):
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "invalid JSON body")
    node_id, budget = _context_body(body, open_request=True)
    result = await _context_graph_call("open", node_id, budget)
    return {"context": result["context"], "tokens": result["tokens"], "id": result["node_id"]}


def publish(event: dict) -> None:
    for q in list(_listeners):
        q.put_nowait(event)


# ---------- daemons ----------
@app.post("/ingest")
async def ingest(body: dict):
    agent = str(body.get("agent", ""))
    if not AGENT_RE.match(agent):
        raise HTTPException(400, "bad agent id")
    counts = await _db(store.ingest, agent, body.get("events") or [])
    publish({"type": "ingest", "agent": agent, **counts})
    return counts


@app.post("/ingest/memories")
async def ingest_memories(body: dict):
    """Results of the LLM step: new memories (written by the agent whose chat they came from)."""
    saved = []
    for m in body.get("memories") or []:
        mid = await _db(lambda m=m: store.remember(m["agent"], m["kind"], m["text"], m.get("topics"),
                                                   short=m.get("short"), src=m.get("source")))
        saved.append(mid)
    for agent, ids in (body.get("done") or {}).items():
        if AGENT_RE.match(agent) and ids:
            await _db(store.mark_extracted, agent, ids)
    if saved:
        publish({"type": "memory", "ids": saved})
    return {"saved": saved}


@app.get("/api/pending")
async def pending(limit: int = 30):
    return await _db(store.pending_items, min(max(limit, 1), 100))


# ---------- pages ----------
@app.get("/api/health")
async def health():
    try:
        await anyio.to_thread.run_sync(store.ping)
        return {"db": "ok", "workspace": settings.workspace}
    except Exception as e:
        return {"db": f"unavailable: {type(e).__name__}", "workspace": settings.workspace}


@app.get("/api/state")
async def state():
    return await _db(store.state)


@app.get("/api/trace/{memory_id}")
async def trace(memory_id: str):
    t = await _db(store.trace, memory_id)
    if not t:
        raise HTTPException(404, "no such memory")
    return t


@app.get("/events")
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _listeners.add(q)

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _listeners.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
