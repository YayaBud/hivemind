"""Build a local, redacted conversation corpus for history retrieval benchmarks."""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import tiktoken
except ImportError:  # pragma: no cover - exercised by CLI guard
    tiktoken = None

from hivemind.redact import redact

MAX_PROTO = 8 * 1024 * 1024
MAX_DEPTH = 12
MAX_JSON_TEXT = 2 * 1024 * 1024
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def token_count(text: str) -> int:
    if tiktoken is None:
        raise RuntimeError("tiktoken is required")
    return len(tiktoken.get_encoding("o200k_base").encode(text))


def _norm(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _text_values(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 8:
        return
    if isinstance(value, str):
        if value and len(value) <= MAX_JSON_TEXT:
            yield value
    elif isinstance(value, list):
        for item in value:
            yield from _text_values(item, depth + 1)


def _codex_text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value and len(value) <= MAX_JSON_TEXT:
            yield value
    elif isinstance(value, list):
        for block in value:
            if not isinstance(block, dict) or block.get("type") not in {"input_text", "output_text", "text"}:
                continue
            text = block.get("text")
            if isinstance(text, str) and len(text) <= MAX_JSON_TEXT:
                yield text


def _looks_injected(text: str, project: str) -> bool:
    low = text.lower()
    if low.startswith("# agents.md instructions") or "<environment_context>" in low or "external_codex_apps_" in low or "<system-reminder>" in low:
        return True
    if text.startswith("<") and text.endswith(">") and not re.sub(r"<[^>]+>", "", text).strip():
        return True
    return False


def _record(agent: str, session: str, role: str, source_path: str, source_pos: str, text: str, component: str = "", timestamp: str | None = None, position_key: str = "source_line") -> dict[str, Any] | None:
    text = redact(text.strip())
    if not text:
        return None
    suffix = f":{component}" if component else ""
    rid = f"{agent}:{source_path}:{source_pos}:{role}{suffix}"
    result = {"id": rid, "agent": agent, "session": session, "role": role, "source_path": source_path, position_key: int(source_pos) if source_pos.isdigit() else source_pos, "text": text}
    if timestamp: result["timestamp"] = timestamp
    return result


def _is_compaction(text: str) -> bool:
    low = text.lower().strip()
    return low.startswith("the messages after this summary are the most recent") or low.startswith("this session is being continued from a previous conversation that ran out of context") or low.startswith("summary of the conversation") or low.startswith("conversation summary:")


def _claude_text(row: dict[str, Any], project: str) -> list[tuple[str, str]]:
    if row.get("isMeta") or "toolUseResult" in row or row.get("type") in {"tool_result", "tool_use"}:
        return []
    role = row.get("role") or row.get("type")
    if role not in {"user", "assistant"}:
        return []
    message = row.get("message")
    content = message.get("content", "") if isinstance(message, dict) else row.get("content", row.get("text", ""))
    out: list[tuple[str, str]] = []
    if isinstance(content, str):
        if not _is_compaction(content) and not (role == "user" and content.lstrip().startswith("<system-reminder>") and content.rstrip().endswith("</system-reminder>")):
            out.append((role, content))
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in {"text", None}:
                continue
            value = block.get("text")
            if isinstance(value, str) and not _is_compaction(value) and not (value.lstrip().startswith("<system-reminder>") and value.rstrip().endswith("</system-reminder>")):
                out.append((role, value))
    return out


def discover_claude(project: Path, root: Path, inv: dict[str, Any]) -> list[dict[str, Any]]:
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project))
    base = root / ".claude" / "projects" / slug
    files = list(base.rglob("*.jsonl")) if base.is_dir() else []
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for path in sorted(files):
        rel = str(path.relative_to(base)).replace("\\", "/")
        session = path.stem
        subagent = "/subagents/" in f"/{rel}/" or path.parent.name == "subagents"
        stats = _agent_stats(inv, "claude", subagent, path)
        try:
            with path.open("rb") as fh:
                for line_no, line in enumerate(fh, 1):
                    if fh.tell() > stats["snapshot_bytes"]: break
                    line = line.decode("utf-8", "replace")
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        stats["errors"].append(f"{rel}:{line_no}: malformed JSON: {exc.msg}")
                        continue
                    for component_no, (role, text) in enumerate(_claude_text(row, str(project)), 1):
                        if role == "user" and text.lstrip().startswith("[Workflow harness"):
                            role = "agent_task"
                        if _looks_injected(text, str(project)):
                            stats["exclusion_reasons"]["injected"] += 1
                            continue
                        key = ("claude", session, role, _norm(text))
                        if key in seen:
                            stats["deduplicated"] += 1
                            continue
                        seen.add(key)
                        rec = _record("claude", session, role, str(path.resolve()), str(line_no), text, f"{'subagent' if subagent else 'root'}-{component_no}", row.get("timestamp"))
                        if rec:
                            records.append(rec)
                            stats["normalized_messages"] += 1
                            stats["chars"] += len(rec["text"])
                            stats["tokens"] += token_count(rec["text"])
        except OSError as exc:
            stats["errors"].append(f"{rel}: {exc}")
    return records


def _agent_stats(inv: dict[str, Any], agent: str, subagent: bool, path: Path) -> dict[str, Any]:
    bucket = inv["agents"][agent]["subagent" if subagent else "root"]
    entry = {"path": str(path.resolve()), "bytes": 0, "snapshot_bytes": 0, "normalized_messages": 0, "chars": 0, "tokens": 0, "errors": [], "exclusion_reasons": Counter(), "deduplicated": 0}
    try:
        entry["bytes"] = path.stat().st_size
        entry["snapshot_bytes"] = entry["bytes"]
    except OSError as exc:
        entry["errors"].append(str(exc))
    bucket["sessions"] += 1
    bucket["files"].append(entry)
    return entry


def _codex_candidate(path: Path, project: Path) -> bool:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in itertools.islice(fh, 6):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") == "session_meta":
                    cwd = row.get("payload", {}).get("cwd", row.get("cwd"))
                    if cwd and os.path.normcase(os.path.normpath(str(cwd))) == os.path.normcase(os.path.normpath(str(project))):
                        source = row.get("payload", {}).get("source", {}) if isinstance(row.get("payload"), dict) else {}
                        outer_source = row.get("source", {})
                        return True, bool((source.get("subagent") if isinstance(source, dict) else False) or (outer_source.get("subagent") if isinstance(outer_source, dict) else False))
    except OSError:
        pass
    return False, False


def discover_codex(project: Path, root: Path, inv: dict[str, Any]) -> list[dict[str, Any]]:
    base = root / ".codex" / "sessions"
    files = [(p, _codex_candidate(p, project)[1]) for p in base.rglob("*.jsonl") if _codex_candidate(p, project)[0]] if base.is_dir() else []
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for path, subagent in sorted(files):
        rel = str(path.relative_to(base)).replace("\\", "/")
        stats = _agent_stats(inv, "codex", subagent, path)
        session = path.stem
        try:
            with path.open("rb") as fh:
                for line_no, line in enumerate(fh, 1):
                    if fh.tell() > stats["snapshot_bytes"]: break
                    line = line.decode("utf-8", "replace")
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        stats["errors"].append(f"{rel}:{line_no}: malformed JSON: {exc.msg}")
                        continue
                    if row.get("type") != "response_item":
                        continue
                    payload = row.get("payload", row)
                    role = payload.get("role") if isinstance(payload, dict) else None
                    if role not in {"user", "assistant"} or payload.get("type") not in {None, "message"}:
                        continue
                    texts = list(_codex_text_values(payload.get("content", payload.get("text", ""))))
                    for component_no, text in enumerate(texts, 1):
                        if "## My request:" in text:
                            text = text.rsplit("## My request:", 1)[1]
                        if _looks_injected(text, str(project)):
                            stats["exclusion_reasons"]["injected"] += 1
                            continue
                        key = ("codex", session, role, _norm(text))
                        if key in seen:
                            stats["deduplicated"] += 1
                            continue
                        seen.add(key)
                        rec = _record("codex", session, role, str(path.resolve()), str(line_no), text, f"content-{component_no}", row.get("timestamp"))
                        if rec:
                            records.append(rec)
                            stats["normalized_messages"] += 1
                            stats["chars"] += len(rec["text"])
                            stats["tokens"] += token_count(rec["text"])
        except OSError as exc:
            stats["errors"].append(f"{rel}: {exc}")
    return records


def decode_wire(data: bytes, depth: int = 0) -> dict[int, list[Any]]:
    if depth > MAX_DEPTH or len(data) > MAX_PROTO:
        raise ValueError("protobuf bounds exceeded")
    out: dict[int, list[Any]] = defaultdict(list)
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        field, wire = key >> 3, key & 7
        if field <= 0 or wire == 4 or wire == 3:
            raise ValueError("malformed protobuf key")
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 1:
            if i + 8 > len(data): raise ValueError("truncated fixed64")
            value, i = data[i:i + 8], i + 8
        elif wire == 2:
            size, i = _varint(data, i)
            if size > MAX_PROTO or i + size > len(data): raise ValueError("truncated length-delimited field")
            value, i = data[i:i + size], i + size
        elif wire == 5:
            if i + 4 > len(data): raise ValueError("truncated fixed32")
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError("unsupported protobuf wire type")
        out[field].append(value)
    return dict(out)


def _varint(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if i >= len(data): raise ValueError("truncated varint")
        byte = data[i]; i += 1
        value |= (byte & 127) << shift
        if byte < 128: return value, i
    raise ValueError("varint overflow")


def _bytes_field(fields: dict[int, list[Any]], field: int) -> list[bytes]:
    return [v for v in fields.get(field, []) if isinstance(v, bytes)]


def _decode_text_blob(blob: bytes) -> str:
    try: return blob.decode("utf-8")
    except UnicodeDecodeError: return ""


def _url_match(text: str, project: Path) -> bool:
    decoded = urllib.parse.unquote(text).replace("\\", "/")
    decoded = re.sub(r"/{2,}", "/", decoded)
    target = str(project).replace("\\", "/").rstrip("/").lower()
    return bool(re.search(re.escape(target) + r"(?:$|[/'\"\s,;)}])", decoded.lower()))


def _ag_strings(blob: bytes, path: tuple[int, ...] = (), depth: int = 0) -> list[tuple[tuple[int, ...], str]]:
    if depth > 4: return []
    try: fields = decode_wire(blob, depth)
    except ValueError: return []
    out: list[tuple[tuple[int, ...], str]] = []
    for number, values in fields.items():
        for idx, value in enumerate(values):
            if isinstance(value, bytes):
                text = _decode_text_blob(value)
                if text and any(ord(c) < 9 or (13 < ord(c) < 32) for c in text) is False:
                    out.append((path + (number, idx), text))
                out.extend(_ag_strings(value, path + (number, idx), depth + 1))
    return out


def discover_antigravity(project: Path, root: Path, inv: dict[str, Any]) -> list[dict[str, Any]]:
    base = root / ".gemini" / "antigravity-ide" / "conversations"
    paths = sorted(base.glob("*.db")) if base.is_dir() else []
    records: list[dict[str, Any]] = []
    for path in paths:
        stats = _agent_stats(inv, "antigravity", False, path)
        try:
            with sqlite3.connect(f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True) as db:
                db.execute("BEGIN")
                rows = db.execute("SELECT idx, step_type, step_payload FROM steps ORDER BY idx").fetchall()
                selected = False
                for idx, step_type, payload in rows:
                    if not isinstance(payload, (bytes, bytearray)): continue
                    try: fields = decode_wire(bytes(payload))
                    except ValueError as exc:
                        stats["errors"].append(f"step {idx}: malformed protobuf: {exc}"); continue
                    strings = _ag_strings(bytes(payload))
                    if any(_url_match(text, project) for _, text in strings): selected = True
                    if step_type == 14:
                        for blob in _bytes_field(fields, 19):
                            try:
                                inner = decode_wire(blob)
                                for text_blob in _bytes_field(inner, 2):
                                    text = _decode_text_blob(text_blob)
                                    if text and _url_match(text, project):
                                        selected = True
                                    if text and not _looks_injected(text, str(project)):
                                        rec = _record("antigravity", path.stem, "user", str(path.resolve()), str(idx), text, "user19.2", "time unknown", "source_step")
                                        if rec: records.append(rec); stats["normalized_messages"] += 1; stats["chars"] += len(rec["text"]); stats["tokens"] += token_count(rec["text"])
                            except ValueError as exc:
                                stats["errors"].append(f"step {idx}: malformed nested protobuf: {exc}")
                    elif step_type == 15:
                        # outer 20.1 is the observed public-text candidate; 20.3 is reasoning.
                        for blob in _bytes_field(fields, 20):
                            try:
                                inner = decode_wire(blob)
                            except ValueError as exc:
                                stats["errors"].append(f"step {idx}: malformed nested protobuf: {exc}"); continue
                            for text_blob in _bytes_field(inner, 1):
                                text = _decode_text_blob(text_blob)
                                if text and selected and not _looks_injected(text, str(project)):
                                    rec = _record("antigravity", path.stem, "assistant", str(path.resolve()), str(idx), text, "assistant20.1", "time unknown", "source_step")
                                    if rec: records.append(rec); stats["normalized_messages"] += 1; stats["chars"] += len(rec["text"]); stats["tokens"] += token_count(rec["text"])
                            for _ in _bytes_field(inner, 3):
                                stats["exclusion_reasons"]["reasoning"] += 1
                if not selected:
                    stats["exclusion_reasons"]["project_mismatch"] += 1
                    records = [r for r in records if r["source_path"] != str(path.resolve())]
                else:
                    inv.setdefault("selected_antigravity", []).append(str(path.resolve()))
        except (OSError, sqlite3.Error) as exc:
            stats["errors"].append(str(exc))
    return records


def _notes(project: Path, output: Path, selected_antigravity: list[str]) -> list[dict[str, Any]]:
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project))
    claude_memory = home / ".claude" / "projects" / slug / "memory"
    paths = [project / n for n in ("memory.md", "best_way.md", "rearch_finding.md", "implementation_plan.md")]
    paths += list(claude_memory.glob("**/*.md")) if claude_memory.is_dir() else []
    for selected_db in selected_antigravity:
        db = Path(selected_db)
        brain = db.parent.parent / "brain" / db.stem
        paths += list(brain.glob("*.md")) if brain.is_dir() else []
    out = []
    for path in sorted(set(p for p in paths if p.is_file())):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            start = 1; chunk: list[str] = []
            for line_no, line in enumerate(lines + [""], 1):
                if line.strip():
                    if not chunk: start = line_no
                    chunk.append(line)
                elif chunk:
                    paragraph = redact("\n".join(chunk).strip())
                    if paragraph:
                        timestamp = None
                        metadata = Path(str(path) + ".metadata.json")
                        if metadata.is_file():
                            try: timestamp = json.loads(metadata.read_text(encoding="utf-8")).get("updatedAt")
                            except (OSError, json.JSONDecodeError): pass
                        item = {"id": f"note:{path}:{start}", "agent": "artifact", "session": path.stem, "role": "artifact", "source_path": str(path.resolve()), "source_line": start, "text": paragraph}
                        if timestamp: item["timestamp"] = timestamp
                        out.append(item)
                    chunk = []; start = line_no + 1
        except OSError: continue
    return out


def _split_records(records: list[dict[str, Any]], limit: int = 600) -> list[dict[str, Any]]:
    enc = tiktoken.get_encoding("o200k_base")
    out = []
    for record in records:
        ids = enc.encode(record["text"])
        if len(ids) <= limit: out.append(record); continue
        for start in range(0, len(ids), limit):
            item = dict(record); item["id"] = f"{record['id']}#chunk{start // limit + 1}"; item["text"] = enc.decode(ids[start:start + limit]); out.append(item)
    return out


def format_context(records: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"[{r['id']}] {r['role']} ({r.get('timestamp', 'time unknown')}): {r['text']}" for r in records)


def retrieve(records: list[dict[str, Any]], query: str, budget_tokens: int) -> list[dict[str, Any]]:
    if budget_tokens <= 0: return []
    candidates = _split_records(records)
    q = Counter(x.lower() for x in _TOKEN_RE.findall(query))
    docs = [Counter(x.lower() for x in _TOKEN_RE.findall(r["text"])) for r in candidates]
    df = Counter(term for doc in docs for term in doc)
    scored = []
    for i, (record, doc) in enumerate(zip(candidates, docs)):
        score = sum((1 + (doc[t] > 0)) * (1 + q[t]) * (1.0 + __import__("math").log((1 + len(docs)) / (1 + df[t]))) for t in q if t in doc)
        if score: scored.append((-score, record["id"], i))
    scored.sort()
    result = []
    used = 0
    for _, _, i in scored:
        cost = token_count(format_context([candidates[i]]))
        if used + cost > budget_tokens: continue
        result.append(candidates[i]); used += cost
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, Counter): return dict(value)
    if isinstance(value, dict): return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list): return [_json_safe(v) for v in value]
    return value


def build(project: Path, output: Path) -> None:
    if tiktoken is None: raise RuntimeError("tiktoken is required")
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    inv: dict[str, Any] = {"snapshot_timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "project": str(project), "tokenizer": "o200k_base", "billing_or_semantic_accuracy": "not measured", "agents": {a: {"root": {"sessions": 0, "files": []}, "subagent": {"sessions": 0, "files": []}} for a in ("claude", "codex", "antigravity")}}
    records = discover_claude(project, home, inv) + discover_codex(project, home, inv) + discover_antigravity(project, home, inv)
    notes = _notes(project, output, inv.get("selected_antigravity", []))
    output.mkdir(parents=True, exist_ok=True)
    with (output / "corpus.jsonl").open("w", encoding="utf-8") as fh:
        for record in records: fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (output / "notes.jsonl").open("w", encoding="utf-8") as fh:
        for record in notes: fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    inv["records"] = len(records); inv["notes"] = len(notes); inv["exact_sum_record_tokens"] = sum(token_count(r["text"]) for r in records)
    inv["agents"] = _json_safe(inv["agents"])
    with (output / "inventory.json").open("w", encoding="utf-8") as fh: json.dump(inv, fh, indent=2)
    with (output / "full_history.txt").open("w", encoding="utf-8") as fh: fh.write(format_context(records))
    print(json.dumps({"records": len(records), "notes": len(notes), "errors": sum(len(x["errors"]) for a in inv["agents"].values() for x in a["root"]["files"] + a["subagent"]["files"]), "output": str(output)}))


def self_test() -> None:
    assert _claude_text({"role": "assistant", "content": [{"type": "text", "text": "hello"}, {"type": "tool_use", "text": "secret"}]}, "x") == [("assistant", "hello")]
    try: json.loads("{")
    except json.JSONDecodeError: pass
    else: raise AssertionError("malformed JSON")
    try: decode_wire(b"\x80")
    except ValueError: pass
    else: raise AssertionError("malformed protobuf")
    assert not _looks_injected("Please inspect AGENTS.md and fix the parser", "x")
    assert _looks_injected("<environment_context>{}</environment_context>", "x")
    assert _claude_text({"role": "user", "content": "<pasted_content>real request</pasted_content>"}, "x")
    assert list(_codex_text_values([{"type": "image", "data": "data:image/png;base64,AAAA"}, {"type": "input_text", "text": "kept"}])) == ["kept"]
    assert _url_match("file:///D:/new game/project", Path("D:/new game"))
    assert not _url_match("file:///D:/new gameplay/project", Path("D:/new game"))
    assert redact("sk-ant-abcdefghijklmnop") == "[redacted]"
    rs = [_record("x", "s", "user", "p", "1", "alpha beta")]
    got = retrieve([r for r in rs if r], "alpha", 20)
    assert got and len(format_context(got)) > 0 and token_count(format_context(got)) <= 20
    assert len({r["id"] for r in got}) == len(got)
    print("self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if tiktoken is None: raise ValueError("tiktoken is required")
        if not args.project or not args.project.is_absolute() or not args.project.is_dir(): raise ValueError("--project must be an existing absolute directory")
        if not args.output or not args.output.is_absolute(): raise ValueError("--output must be an absolute directory")
        build(args.project.resolve(), args.output.resolve())
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
