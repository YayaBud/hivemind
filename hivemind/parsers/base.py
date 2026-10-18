"""Shared turn bookkeeping for the log readers."""
import re

MEMORY_ID = re.compile(r"\bm_[0-9a-f]{12}\b")
HIVEMIND_READ_TOOL = re.compile(r"hivemind.*(recall|sync)$", re.I)


class TurnParser:
    """Tracks, per log file: the session, the current turn (your message), the previous item in that
    turn, the latest thought, and Hivemind recall calls waiting for their result."""
    agent = ""

    def __init__(self, state: dict | None = None):
        self.s = state if state is not None else {}
        self.s.setdefault("pending", {})

    def _session(self, sid: str, cwd: str | None, ts: str | None) -> list[dict]:
        if self.s.get("sid") == sid:
            return []
        self.s.update(sid=sid, cwd=cwd, turn=None, prev=None, prev_label=None, last_thought=None, titled=False)
        return [{"kind": "session", "id": sid, "cwd": cwd, "ts": ts, "title": None}]

    def _user(self, mid: str, text: str, ts) -> list[dict]:
        ev = [{"kind": "message", "id": mid, "session": self.s["sid"], "role": "user", "text": text, "ts": ts,
               "turn": mid, "prev": self.s.get("prev"), "prev_label": self.s.get("prev_label")}]
        if not self.s.get("titled"):
            ev.append({"kind": "session", "id": self.s["sid"], "cwd": self.s.get("cwd"), "ts": ts,
                       "title": " ".join(text.split())[:70]})
            self.s["titled"] = True
        self.s.update(turn=mid, prev=mid, prev_label="Message", last_thought=None)
        return ev

    def _thought(self, tid: str, text: str, fidelity: str, ts) -> list[dict]:
        ev = {"kind": "thought", "id": tid, "session": self.s["sid"], "text": text, "fidelity": fidelity, "ts": ts,
              "turn": self.s.get("turn"), "prev": self.s.get("prev"), "prev_label": self.s.get("prev_label")}
        self.s.update(prev=tid, prev_label="Thought", last_thought=tid)
        return [ev]

    def _reply(self, mid: str, text: str, ts) -> list[dict]:
        ev = {"kind": "message", "id": mid, "session": self.s["sid"], "role": "assistant", "text": text, "ts": ts,
              "turn": self.s.get("turn"), "prev": self.s.get("prev"), "prev_label": self.s.get("prev_label")}
        self.s.update(prev=mid, prev_label="Message")
        return [ev]

    def _tool_call(self, call_id: str, name: str) -> None:
        if call_id and HIVEMIND_READ_TOOL.search(name or ""):
            self.s["pending"][call_id] = self.s.get("last_thought")

    def _tool_result(self, call_id: str, output) -> list[dict]:
        if call_id not in self.s["pending"]:
            return []
        thought = self.s["pending"].pop(call_id)
        ids = sorted(set(MEMORY_ID.findall(output if isinstance(output, str) else str(output))))
        return [{"kind": "recall", "session": self.s.get("sid"), "thought": thought, "memory_ids": ids}] if ids else []
