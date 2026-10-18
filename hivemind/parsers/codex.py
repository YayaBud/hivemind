"""Codex rollouts (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl) → events.
Codex logs only summaries of its reasoning (the rest is encrypted), so thoughts are 'summary'."""
import json

from .base import TurnParser


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict)
                     and c.get("type") in ("input_text", "output_text", "text"))


class CodexParser(TurnParser):
    agent = "codex"
    glob = "~/.codex/sessions/**/rollout-*.jsonl"

    def feed(self, line: str) -> list[dict]:
        try:
            o = json.loads(line)
        except ValueError:
            return []
        p, ts = o.get("payload") or {}, o.get("timestamp")
        if not isinstance(p, dict):
            return []
        if o.get("type") == "session_meta":
            return self._session("cx:" + str(p.get("id") or p.get("session_id")), p.get("cwd"), p.get("timestamp") or ts)
        if o.get("type") != "response_item" or not self.s.get("sid"):
            return []
        rid = "cx:" + str(p.get("id") or f"{self.s['sid']}:{o.get('ordinal')}")
        kind = p.get("type")
        if kind == "message":
            text = _text(p.get("content")).strip()
            if not text or text.startswith("<"):  # environment context and injected instructions
                return []
            if p.get("role") == "user":
                return self._user(rid, text, ts)
            if p.get("role") == "assistant":
                return self._reply(rid, text, ts)
            return []
        if kind == "reasoning":
            events = []
            for i, part in enumerate(p.get("summary") or []):
                if isinstance(part, dict) and (part.get("text") or "").strip():
                    events += self._thought(f"{rid}:{i}", part["text"].strip(), "summary", ts)
            return events
        if kind in ("function_call", "custom_tool_call"):
            self._tool_call(p.get("call_id"), p.get("name"))
            return []
        if kind in ("function_call_output", "custom_tool_call_output"):
            return self._tool_result(p.get("call_id"), p.get("output"))
        return []
