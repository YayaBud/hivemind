"""Claude Code transcripts (~/.claude/projects/<slug>/<session>.jsonl) → events."""
import json

from .base import TurnParser


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


class ClaudeCodeParser(TurnParser):
    agent = "claude-code"
    glob = "~/.claude/projects/*/*.jsonl"

    def feed(self, line: str) -> list[dict]:
        try:
            o = json.loads(line)
        except ValueError:
            return []
        if o.get("type") not in ("user", "assistant") or o.get("isSidechain") or o.get("isMeta"):
            return []
        sid, uid, ts = "cc:" + str(o.get("sessionId")), str(o.get("uuid")), o.get("timestamp")
        events = self._session(sid, o.get("cwd"), ts)
        content = (o.get("message") or {}).get("content")
        if o["type"] == "user":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        events += self._tool_result(b.get("tool_use_id"), b.get("content"))
            text = _text(content).strip()
            # system reminders, command echoes and tool output arrive as "user" lines too
            if text and not text.startswith("<") and not o.get("toolUseResult"):
                events += self._user("cc:" + uid, text, ts)
            return events
        for i, b in enumerate(content if isinstance(content, list) else []):
            if not isinstance(b, dict):
                continue
            bid = f"cc:{uid}:{i}"
            if b.get("type") == "thinking" and (b.get("thinking") or "").strip():
                events += self._thought(bid, b["thinking"].strip(), "full", ts)
            elif b.get("type") == "text" and (b.get("text") or "").strip():
                events += self._reply(bid, b["text"].strip(), ts)
            elif b.get("type") == "tool_use":
                self._tool_call(b.get("id"), b.get("name"))
        return events
