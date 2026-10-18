"""Strip secrets from text before it is stored or sent to an LLM."""
import re

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"), "[redacted]"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}"), "[redacted]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[redacted]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[redacted]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[redacted]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [redacted]"),
    # KEY=value / key: value for secret-looking names; keeps the name, hides the value
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|secret|token|password|passwd)[A-Z0-9_]*)(\s*[=:]\s*)([\"']?)[^\s\"']{6,}\3"), r"\1\2[redacted]"),
]


def redact(text: str | None) -> str:
    if not text:
        return ""
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text
