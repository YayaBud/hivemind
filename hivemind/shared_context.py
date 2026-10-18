"""Process-local derived retrieval cache backed by the authoritative Store."""
from __future__ import annotations

import threading
from typing import Any

from .context_graph import ContextGraph
from .store import Store


class SharedContextGraph:
    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.RLock()
        self._revision: int | None = None
        self._graph: ContextGraph | None = None

    def _refresh(self) -> ContextGraph:
        revision = self.store.context_revision()
        with self._lock:
            if self._graph is not None and self._revision == revision:
                return self._graph
            snapshot = None
            for _ in range(2):
                snapshot = self.store.context_snapshot()
                if snapshot.get("consistent", True): break
            if snapshot is None or not snapshot.get("consistent", True):
                raise RuntimeError("context snapshot changed during bounded read")
            self._graph = ContextGraph(snapshot["records"], snapshot["notes"])
            self._revision = snapshot["revision"]
            return self._graph

    def search(self, query: str, budget_tokens: int = 1200) -> dict[str, Any]:
        graph = self._refresh()
        result = graph.search(query, budget_tokens, "hybrid")
        result["cache_revision"] = self._revision
        if not result["cards"]:
            result["context"] = "[no relevant memory]"
            result["tokens"] = graph._count(result["context"])
        return result

    def open(self, node_id: str, budget_tokens: int = 1200) -> dict[str, Any]:
        graph = self._refresh()
        result = graph.open(node_id, budget_tokens)
        result["cache_revision"] = self._revision
        return result
