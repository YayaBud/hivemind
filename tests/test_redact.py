from hivemind.redact import redact

# Fake secrets built at runtime so this file never contains a real-looking key literal.
FAKE = {
    "anthropic": "sk-ant-" + "a1" * 20,
    "openai": "sk-proj-" + "Z9" * 12,
    "github": "ghp_" + "x" * 36,
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "google": "AIza" + "b" * 35,
}


def test_known_key_shapes_are_removed():
    for name, key in FAKE.items():
        out = redact(f"here is my {name} key {key} ok")
        assert key not in out, name
        assert "[redacted]" in out, name


def test_assignment_keeps_the_name_hides_the_value():
    out = redact('FALKORDB_PASSWORD="hunter22secret" and api_key: abcdef123456')
    assert "hunter22secret" not in out and "abcdef123456" not in out
    assert "FALKORDB_PASSWORD" in out and "api_key" in out


def test_private_key_block_and_bearer():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    out = redact(f"x {pem} y Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
    assert "MIIabc" not in out and "abcdefghijklmnopqrstuvwxyz" not in out


def test_plain_text_untouched():
    text = "The expiry check uses < instead of <=, tokens pass at expiry."
    assert redact(text) == text
    assert redact(None) == ""
