"""The LLM step, run on the user's own subscription CLI (or Ollama / an optional API key)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .config import settings

BACKENDS = ("claude-cli", "codex-cli", "gemini-cli", "ollama", "api")
KINDS = ("finding", "decision", "fact", "howto")

EXTRACT_PROMPT = """You extract durable team memory from coding-agent chats and thinking.
Return ONLY a JSON array, no prose. Each element:
{{"kind": "finding|decision|fact|howto", "text": "one plain sentence", "short": "at most 5 words",
 "topics": ["1 to 3 short topic names"], "source": "the [id] of the item it came from"}}
Keep only what another agent on this project would need later: bugs found, decisions made, facts
about the code or infrastructure, how to do something. Skip chit-chat, half-finished plans, and
anything not yet true. Return [] if nothing qualifies.

ITEMS:
{items}
"""


def _run_dir() -> str:
    # Run CLIs outside the allowlisted project, so their own transcripts are never re-ingested.
    d = settings.state_dir / "llm-runs"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _cli(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError(f"{name} is not installed or not on PATH")
    return exe


def complete(prompt: str, backend: str | None = None, model: str | None = None, timeout: int = 300) -> str:
    backend = backend or settings.llm
    model = model or settings.llm_model
    if backend == "claude-cli":
        args = [_cli("claude"), "-p", "--output-format", "text"] + (["--model", model] if model else [])
        return _proc(args, prompt, timeout)
    if backend == "codex-cli":
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "last.txt"
            args = [_cli("codex"), "exec", "--skip-git-repo-check", "-s", "read-only", "-o", str(out)] + \
                   (["-m", model] if model else []) + ["-"]
            _proc(args, prompt, timeout)
            return out.read_text(encoding="utf-8") if out.exists() else ""
    if backend == "gemini-cli":
        args = [_cli("gemini"), "-p", "Follow the instructions above.", "-o", "text"] + (["-m", model] if model else [])
        return _proc(args, prompt, timeout)
    if backend in ("ollama", "api"):
        base = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1" if backend == "ollama" else "https://api.openai.com/v1")
        key = os.environ.get("OPENAI_API_KEY", "ollama")
        body = json.dumps({"model": model or ("llama3.1" if backend == "ollama" else "gpt-4o-mini"),
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions", body,
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    raise ValueError(f"unknown LLM backend {backend!r}; use one of {BACKENDS}")


def _proc(args: list[str], prompt: str, timeout: int) -> str:
    r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                       timeout=timeout, cwd=_run_dir())
    if r.returncode != 0:
        raise RuntimeError(f"{Path(args[0]).name} exited {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout


def extraction_prompt(items: list[dict], max_chars: int = 12000) -> str:
    lines, used = [], 0
    for it in items:
        who = f"{it['agent']}, {'thinking' if it['type'] == 'thought' else it.get('role') or 'message'}"
        line = f"[{it['id']}] ({who}) {' '.join((it.get('text') or '').split())[:600]}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return EXTRACT_PROMPT.format(items="\n".join(lines))


def parse_memories(raw: str, valid_sources: set[str]) -> list[dict]:
    """Pull the JSON array out of an LLM reply and keep only well-formed memories."""
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        kind, text = d.get("kind"), " ".join(str(d.get("text") or "").split())
        src = str(d.get("source") or "").strip("[] ")
        if kind not in KINDS or len(text) < 8:
            continue
        topics = [str(t)[:40] for t in (d.get("topics") or []) if str(t).strip()][:3]
        out.append({"kind": kind, "text": text[:400], "short": str(d.get("short") or "")[:40] or None,
                    "topics": topics, "source": src if src in valid_sources else None})
    return out
