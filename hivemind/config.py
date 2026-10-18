"""Settings from the environment (and an optional .env at the project root, never logged)."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """KEY=VALUE lines; variables already in the environment win. Values are never printed."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    falkor_host: str = os.environ.get("FALKORDB_HOST", "localhost")
    falkor_port: int = int(os.environ.get("FALKORDB_PORT", "6379"))
    falkor_username: str | None = os.environ.get("FALKORDB_USERNAME") or None
    falkor_password: str | None = os.environ.get("FALKORDB_PASSWORD") or None
    falkor_ssl: bool = _flag("FALKORDB_SSL")
    workspace: str = os.environ.get("HIVEMIND_WORKSPACE", "team")
    # ponytail: one shared token per workspace; per-agent tokens if teams ever share a server
    token: str = os.environ.get("HIVEMIND_TOKEN", "dev-token")
    server: str = os.environ.get("HIVEMIND_SERVER", "http://127.0.0.1:5199")
    # chats are ingested only when their working folder is under one of these (';'-separated)
    allow: tuple[str, ...] = tuple(p for p in os.environ.get("HIVEMIND_ALLOW", str(ROOT)).split(";") if p.strip())
    llm: str = os.environ.get("HIVEMIND_LLM", "claude-cli")
    llm_model: str | None = os.environ.get("HIVEMIND_LLM_MODEL") or None
    state_dir: Path = Path(os.environ.get("HIVEMIND_STATE", str(Path.home() / ".hivemind")))


settings = Settings()


def allowed_cwd(cwd: str | None, allow: tuple[str, ...] = settings.allow) -> bool:
    """True when a chat's working folder is inside the allowlist (case-insensitive on Windows paths)."""
    if not cwd:
        return False
    norm = lambda p: os.path.normcase(os.path.abspath(p)).rstrip("\\/")
    c = norm(cwd)
    return any(c == norm(a) or c.startswith(norm(a) + os.sep) for a in allow)
