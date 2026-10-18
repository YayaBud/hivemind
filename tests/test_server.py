"""Server smoke tests that need no database: auth, health, MCP handshake and tool list."""
import pytest
from fastapi.testclient import TestClient

from hivemind.config import settings
from hivemind.server import app

AUTH = {"Authorization": f"Bearer {settings.token}"}
MCP_HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "Content-Type": "application/json", "X-Agent-Id": "codex"}


@pytest.fixture(scope="module")
def c():
    # one lifespan per process (the MCP session manager starts once); real agents connect via
    # 127.0.0.1 and MCP rejects unknown Host headers (DNS-rebinding guard)
    with TestClient(app, base_url="http://127.0.0.1:5199") as client:
        yield client


def test_private_routes_need_the_token(c):
    assert c.post("/ingest", json={"agent": "codex", "events": []}).status_code == 401
    assert c.post("/mcp", json={}).status_code == 401
    assert c.get("/api/pending").status_code == 401


def test_health_reports_db_state_without_crashing(c):
    r = c.get("/api/health")
    assert r.status_code == 200 and "db" in r.json()


def test_mcp_lists_the_same_tools_for_everyone(c):
    init = c.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
    assert init.status_code == 200, init.text
    assert "Hivemind is the team's shared memory" in init.text
    tools = c.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {t["name"] for t in tools.json()["result"]["tools"]}
    assert {"sync", "recall", "remember", "peers", "handoff", "respond", "trace", "checkpoint", "graph_search", "graph_open"} <= names


def test_pages_are_served(c):
    assert c.get("/").status_code == 200 and "Hivemind" in c.get("/").text
    assert c.get("/rooms.html").status_code == 200
