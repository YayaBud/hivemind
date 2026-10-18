"""Regenerate ATLAS.md: every source module with its line count, role and top-level symbols.

Run after changing module layout or public symbols:  python scripts/gen_atlas.py
"""
import ast
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_DIRS = ["hivemind", "scripts", "tests"]
JS_FILES = ["web/data.js", "web/api.js", "web/index.html", "web/rooms.html", "deck/build.js"]
JS_SYMBOL = re.compile(r"^\s*(?:async\s+)?function\s+(\w+)|^\s*(?:const|let|class)\s+([A-Z_a-z]\w*)\s*=\s*(?:\(|async|class|function|\{|new)|^\s*class\s+(\w+)", re.M)


def py_entry(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    role = doc[0] if doc else ""
    syms = [f"{n.name} ~{n.lineno}" for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return f"- `{path.relative_to(ROOT).as_posix()}` ({src.count(chr(10)) + 1} lines) {role}\n  " + (", ".join(syms) or "—")


def js_entry(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    syms = []
    for m in JS_SYMBOL.finditer(src):
        name = next(g for g in m.groups() if g)
        syms.append(f"{name} ~{src.count(chr(10), 0, m.start()) + 1}")
    return f"- `{path.relative_to(ROOT).as_posix()}` ({src.count(chr(10)) + 1} lines)\n  " + (", ".join(syms[:40]) or "—")


def main() -> None:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        rev = ""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# ATLAS (generated — do not edit; run `python scripts/gen_atlas.py`)", "",
             f"Generated {stamp}" + (f" at {rev}" if rev else " (not a git repo)"), ""]
    for d in PY_DIRS:
        files = sorted((ROOT / d).rglob("*.py")) if (ROOT / d).exists() else []
        if files:
            lines += [f"## {d}/", ""] + [py_entry(f) for f in files if "fixtures" not in f.parts] + [""]
    lines += ["## web and deck", ""] + [js_entry(ROOT / f) for f in JS_FILES if (ROOT / f).exists()] + [""]
    (ROOT / "ATLAS.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote ATLAS.md")


if __name__ == "__main__":
    main()
