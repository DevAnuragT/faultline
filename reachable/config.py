"""Central configuration. Secrets come from the environment or a local .env (never committed)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
SRC_CACHE = CACHE / "src"
DEMO_APP = ROOT / "demo_app"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.environ.get("FALKORDB_PASSWORD") or None
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GRAPH_NAME = os.environ.get("GRAPH_NAME", "reachable")

for d in (CACHE, SRC_CACHE):
    d.mkdir(parents=True, exist_ok=True)
