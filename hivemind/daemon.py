"""The daemon on each laptop.

  python -m hivemind.daemon collect [--once]     read Claude Code + Codex chat and thinking, send it
  python -m hivemind.daemon extract [--once]     the LLM step: turn new chat into shared memories
  python -m hivemind.daemon connect              print the MCP config for each agent
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

from . import llm
from .config import allowed_cwd, settings
from .parsers import PARSERS


# ---------- talking to the server ----------
def call(path: str, body: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(settings.server.rstrip("/") + path,
                                 json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {settings.token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


# ---------- collect ----------
def _state_path():
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / "collect.json"


def collect_once(state: dict) -> dict:
    """One pass over every log file: read new lines, send allowlisted events, advance offsets."""
    totals: dict[str, dict] = {}
    for agent, Parser in PARSERS.items():
        for path in glob.glob(os.path.expanduser(Parser.glob), recursive=True):
            st = state.setdefault(path, {"offset": 0, "parser": {}})
            size = os.path.getsize(path)
            if size < st["offset"]:  # file was replaced
                st.update(offset=0, parser={})
            if size == st["offset"]:
                continue
            with open(path, "rb") as f:
                f.seek(st["offset"])
                chunk = f.read()
            end = chunk.rfind(b"\n") + 1  # only whole lines; the tail waits for the next pass
            if end == 0:
                continue
            parser = Parser(st["parser"])
            events = []
            for line in chunk[:end].decode("utf-8", "replace").splitlines():
                events += parser.feed(line)
            if events and allowed_cwd(parser.s.get("cwd")):
                for i in range(0, len(events), 400):
                    counts = call("/ingest", {"agent": agent, "events": events[i:i + 400]})
                    t = totals.setdefault(agent, {})
                    for k, v in counts.items():
                        t[k] = t.get(k, 0) + v
            st["offset"] += end  # advance only after the server accepted the batch
            st["parser"] = parser.s
    return totals


def collect(once: bool, interval: float) -> None:
    path = _state_path()
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    while True:
        try:
            totals = collect_once(state)
            path.write_text(json.dumps(state), encoding="utf-8")
            if totals:
                print("collected", json.dumps(totals), flush=True)
        except (urllib.error.URLError, OSError) as e:
            print("server not reachable, will retry:", e, file=sys.stderr, flush=True)
        if once:
            return
        time.sleep(interval)


# ---------- extract (the LLM step) ----------
def extract_once(backend: str | None, limit: int = 30) -> dict:
    items = call("/api/pending?limit=%d" % limit)
    if not items:
        return {"items": 0, "memories": 0}
    raw = llm.complete(llm.extraction_prompt(items), backend)
    by_id = {it["id"]: it for it in items}
    mems = llm.parse_memories(raw, set(by_id))
    for m in mems:
        src = by_id.get(m["source"]) if m["source"] else None
        m["agent"] = src["agent"] if src else items[0]["agent"]
    done: dict[str, list[str]] = {}
    for it in items:
        done.setdefault(it["agent"], []).append(it["id"])
    call("/ingest/memories", {"memories": mems, "done": done})
    return {"items": len(items), "memories": len(mems)}


def extract(once: bool, interval: float, backend: str | None) -> None:
    while True:
        try:
            r = extract_once(backend)
            if r["items"]:
                print("extracted", json.dumps(r), flush=True)
        except Exception as e:  # LLM or server failure: items stay pending and are retried
            print("extract failed, will retry:", e, file=sys.stderr, flush=True)
        if once:
            return
        time.sleep(interval)


# ---------- connect ----------
def connect() -> None:
    url = settings.server.rstrip("/") + "/mcp"
    print(f"""Set HIVEMIND_TOKEN in your shell first (the same value as in the server's .env).

Claude Code:
  claude mcp add --transport http hivemind {url} --header "Authorization: Bearer $HIVEMIND_TOKEN" --header "X-Agent-Id: claude-code"

Codex (~/.codex/config.toml):
  [mcp_servers.hivemind]
  url = "{url}"
  [mcp_servers.hivemind.http_headers]
  Authorization = "Bearer <your HIVEMIND_TOKEN>"
  X-Agent-Id = "codex"

Gemini CLI (~/.gemini/settings.json):
  {{"mcpServers": {{"hivemind": {{"httpUrl": "{url}", "headers": {{"Authorization": "Bearer <token>", "X-Agent-Id": "gemini"}}}}}}}}
""")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="hivemind.daemon")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect"); c.add_argument("--once", action="store_true"); c.add_argument("--interval", type=float, default=3)
    e = sub.add_parser("extract"); e.add_argument("--once", action="store_true"); e.add_argument("--interval", type=float, default=180)
    e.add_argument("--llm", choices=llm.BACKENDS, default=None)
    sub.add_parser("connect")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        collect(a.once, a.interval)
    elif a.cmd == "extract":
        extract(a.once, a.interval, a.llm)
    else:
        connect()


if __name__ == "__main__":
    main()
