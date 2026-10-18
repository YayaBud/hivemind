"""Authenticated local graph access without requiring FalkorDB."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from hivemind import mcp_tools
from hivemind.config import settings
from hivemind.server import app


AUTH = {"Authorization": f"Bearer {settings.token}"}


class FakeGraph:
    def __init__(self):
        self.calls = []

    def search(self, query, budget_tokens):
        self.calls.append(("search", query, budget_tokens))
        return {"context": "evidence", "tokens": 3, "cards": [{"id": "n_1"}], "provenance": {"n_1": {"source_path": "secret"}}}

    def open(self, node_id, budget_tokens):
        self.calls.append(("open", node_id, budget_tokens))
        if node_id == "missing":
            raise ValueError("unknown node id")
        return {"context": "expanded", "tokens": 4, "node_id": node_id, "provenance": {"source_path": "secret"}}


@pytest.fixture
def fake_graph():
    graph = FakeGraph()
    mcp_tools.bind_context(graph)
    yield graph
    mcp_tools.bind_context(None)


def request(method, path, **kwargs):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:5199") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def test_context_routes_require_auth():
    assert request("POST", "/api/context/search", json={"query": "x"}).status_code == 401
    assert request("POST", "/api/context/open", json={"node_id": "n_1"}).status_code == 401


def test_auth_rejects_before_graph_load(monkeypatch):
    monkeypatch.setattr(mcp_tools, "local_graph", lambda: (_ for _ in ()).throw(AssertionError("graph loaded")))
    assert request("POST", "/api/context/search", json={"query": "x"}).status_code == 401


def test_context_search_and_open_use_bound_graph_without_store(fake_graph, monkeypatch):
    monkeypatch.setattr("hivemind.server.store", type("NoStore", (), {"search": lambda *_: (_ for _ in ()).throw(AssertionError())})())
    search = request("POST", "/api/context/search", headers=AUTH, json={"query": "x", "budget_tokens": 256})
    opened = request("POST", "/api/context/open", headers=AUTH, json={"node_id": "n_1", "budget_tokens": 512})
    assert search.status_code == 200 and search.json() == {"context": "evidence", "tokens": 3, "node_ids": ["n_1"]}
    assert opened.status_code == 200 and opened.json() == {"context": "expanded", "tokens": 4, "id": "n_1"}
    assert fake_graph.calls == [("search", "x", 256), ("open", "n_1", 512)]


@pytest.mark.parametrize("payload", [
    {"query": "x", "budget_tokens": True},
    {"query": "x", "budget_tokens": "256"},
    {"query": "x", "budget_tokens": 0},
    {"query": "x", "budget_tokens": 4001},
    {"query": "x" * 4001},
    {"query": "   "},
])
def test_context_search_rejects_bad_input(fake_graph, payload):
    assert request("POST", "/api/context/search", headers=AUTH, json=payload).status_code == 400


def test_context_open_unknown_node_is_404(fake_graph):
    response = request("POST", "/api/context/open", headers=AUTH, json={"node_id": "missing"})
    assert response.status_code == 404


@pytest.mark.parametrize("failure", [OSError("socket"), ConnectionError("socket")])
def test_graph_backend_failures_are_503(failure):
    class BrokenGraph:
        def search(self, *_args):
            raise failure

    mcp_tools.bind_context(BrokenGraph())
    try:
        response = request("POST", "/api/context/search", headers=AUTH, json={"query": "x"})
        assert response.status_code == 503
        assert "socket" not in response.text
    finally:
        mcp_tools.bind_context(None)


@pytest.mark.parametrize("node_id", ["", "   ", "x" * 4001])
def test_context_open_rejects_bad_node_id(fake_graph, node_id):
    assert request("POST", "/api/context/open", headers=AUTH, json={"node_id": node_id}).status_code == 400


def test_unbound_store_is_503(monkeypatch):
    mcp_tools.bind(None)
    response = request("POST", "/api/context/search", headers=AUTH, json={"query": "x"})
    assert response.status_code == 503
    assert "context graph unavailable" in response.text


def test_mcp_graph_tools_return_context_only(fake_graph, monkeypatch):
    monkeypatch.setattr(mcp_tools, "_agent", lambda _ctx: "codex")
    assert asyncio.run(mcp_tools.graph_search("x", object())) == "evidence"
    assert asyncio.run(mcp_tools.graph_open("n_1", object())) == "expanded"


def test_local_graph_loader_is_cached_thread_safely(monkeypatch):
    class LoadedGraph:
        pass

    loaded = LoadedGraph()
    calls = []
    monkeypatch.setattr("hivemind.shared_context.SharedContextGraph", lambda store: (calls.append(store), loaded)[1])
    first_store = object()
    mcp_tools.bind(first_store)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            graphs = list(pool.map(lambda _: mcp_tools.local_graph(), range(16)))
        assert graphs == [loaded] * 16
        assert calls == [first_store]
        second_store = object()
        mcp_tools.bind(second_store)
        assert mcp_tools.local_graph() is loaded
        assert calls == [first_store, second_store]
    finally:
        mcp_tools.bind(None)
