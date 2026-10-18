"""Import a frozen local context snapshot into one explicitly local FalkorDB workspace."""
from __future__ import annotations

import argparse, dataclasses, hashlib, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from hivemind.config import settings
from hivemind.context_graph import ContextGraph
from hivemind.redact import redact
from hivemind.store import Store

WS = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$")

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def load(directory: Path) -> tuple[list[dict], list[dict], dict[str, str]]:
    if not directory.is_absolute() or not directory.is_dir(): raise ValueError("directory must be existing absolute frozen directory")
    inventory = directory / "inventory.json"
    if not inventory.is_file(): raise ValueError("missing inventory.json")
    meta = json.loads(inventory.read_text(encoding="utf-8"))
    if not isinstance(meta.get("project"), str) or not meta["project"]: raise ValueError("inventory project metadata required")
    paths = {name: directory / name for name in ("corpus.jsonl", "notes.jsonl")}
    hashes = {name: sha(path) for name, path in paths.items()}
    rows = []
    for name, path in paths.items():
        if not path.is_file(): raise ValueError(f"missing {name}")
        rows.append([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])
    ContextGraph(rows[0], rows[1])
    return rows[0], rows[1], hashes

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", type=Path, required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--port", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        if not WS.fullmatch(args.workspace) or "__" in args.workspace: raise ValueError("invalid workspace")
        if args.port is not None and not 1 <= args.port <= 65535: raise ValueError("port must be 1..65535")
        records, notes, before = load(args.directory.resolve())
        result = {"records": len(records), "notes": len(notes), "corpus_sha256": before["corpus.jsonl"], "notes_sha256": before["notes.jsonl"], "workspace": args.workspace, "dry_run": args.dry_run}
        if not args.dry_run:
            port = args.port or 6379
            local = dataclasses.replace(settings, workspace=args.workspace, falkor_host="127.0.0.1", falkor_port=port, falkor_username=None, falkor_password=None, falkor_ssl=False)
            result["import"] = Store(local).import_context(records, notes)
        after = {name: sha(args.directory.resolve() / name) for name in before}
        if after != before: raise AssertionError("frozen input changed during import")
        result["unchanged"] = True
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (OSError, ValueError, RuntimeError, AssertionError, json.JSONDecodeError) as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main())
