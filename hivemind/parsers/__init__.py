"""Log readers: each turns one agent's transcript lines into Hivemind events.

Event shapes (all ids are globally unique strings):
  {"kind": "session", "id", "cwd", "ts", "title"}
  {"kind": "message", "id", "session", "role": "user"|"assistant", "text", "ts", "turn", "prev", "prev_label"}
  {"kind": "thought", "id", "session", "text", "fidelity": "full"|"summary", "ts", "turn", "prev", "prev_label"}
  {"kind": "recall",  "session", "thought": id|None, "memory_ids": [...]}
A parser keeps a small JSON-serialisable state per log file, so tailing can resume mid-file.
"""
from .claude_code import ClaudeCodeParser
from .codex import CodexParser

PARSERS = {p.agent: p for p in (ClaudeCodeParser, CodexParser)}
