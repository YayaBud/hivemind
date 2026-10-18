from hivemind.llm import extraction_prompt, parse_memories

ITEMS = [{"agent": "codex", "id": "cx:a1", "type": "message", "role": "assistant", "text": "The expiry check uses < instead of <=."},
         {"agent": "codex", "id": "cx:r1:0", "type": "thought", "role": None, "text": "Look at the auth middleware."}]


def test_prompt_lists_items_with_ids():
    p = extraction_prompt(ITEMS)
    assert "[cx:a1] (codex, assistant)" in p and "[cx:r1:0] (codex, thinking)" in p
    assert "JSON array" in p


def test_parse_keeps_valid_memories_only():
    raw = """Here you go:
    [{"kind": "finding", "text": "Expired tokens pass auth because the check uses <.", "short": "tokens pass at expiry",
      "topics": ["token expiry", "auth"], "source": "[cx:a1]"},
     {"kind": "opinion", "text": "nice code", "source": "cx:a1"},
     {"kind": "fact", "text": "short", "source": "cx:a1"},
     {"kind": "howto", "text": "Run the auth tests with pytest tests/auth -x.", "topics": [], "source": "made-up"}]"""
    out = parse_memories(raw, {"cx:a1", "cx:r1:0"})
    assert [m["kind"] for m in out] == ["finding", "howto"]
    assert out[0]["source"] == "cx:a1" and out[0]["topics"] == ["token expiry", "auth"]
    assert out[1]["source"] is None  # unknown source ids are dropped, not trusted


def test_parse_garbage():
    assert parse_memories("no json here", set()) == []
    assert parse_memories("[not valid", set()) == []
    assert parse_memories("[]", set()) == []
