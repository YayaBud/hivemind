import pytest

from hivemind.shared_context import SharedContextGraph


class FakeStore:
    def __init__(self):
        self.revision = 0
        self.rows = [{"id": "m1", "agent": "codex", "session": "s", "role": "user", "text": "persistent fact"}]

    def context_revision(self):
        return self.revision

    def context_snapshot(self):
        return {"revision": self.revision, "records": list(self.rows), "notes": [], "consistent": True}


def test_cache_refreshes_after_revision_change():
    store = FakeStore(); graph = SharedContextGraph(store)
    first = graph.search("persistent", 128)
    store.revision = 1; store.rows.append({"id": "m2", "agent": "codex", "session": "s", "role": "user", "text": "new durable fact"})
    second = graph.search("durable", 128)
    assert first["cache_revision"] == 0
    assert second["cache_revision"] == 1


def test_separate_stores_do_not_share_cache():
    one, two = FakeStore(), FakeStore(); two.rows[0]["text"] = "other tenant"
    assert "persistent fact" in SharedContextGraph(one).search("persistent", 128)["context"]
    assert SharedContextGraph(two).search("persistent", 128)["cards"] == []


def test_db_failure_is_visible():
    class Broken(FakeStore):
        def context_revision(self):
            raise ConnectionError("FalkorDB unavailable")
    with pytest.raises(ConnectionError): SharedContextGraph(Broken()).search("x", 128)


def test_snapshot_notes_keep_required_id():
    store = FakeStore()
    store.context_snapshot = lambda: {"revision": 0, "records": [], "notes": [{"id": "note-1", "text": "durable note", "agent": "artifact", "session": "plan", "role": "artifact"}], "consistent": True}
    result = SharedContextGraph(store).search("durable", 128)
    assert result["cards"][0]["source_id"] == "note-1"
