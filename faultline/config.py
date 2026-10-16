"""Configuration from the environment or a local .env (never committed)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))
FALKORDB_USERNAME = os.environ.get("FALKORDB_USERNAME") or None
FALKORDB_PASSWORD = os.environ.get("FALKORDB_PASSWORD") or None
FALKORDB_SSL = os.environ.get("FALKORDB_SSL", "").lower() in ("1", "true", "yes")
GRAPH_NAME = os.environ.get("GRAPH_NAME", "faultline")
JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686").rstrip("/")
HOTROD_URL = os.environ.get("HOTROD_URL", "http://localhost:8080").rstrip("/")
ROOT_SERVICE = os.environ.get("ROOT_SERVICE", "frontend")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACK_MODELS", "gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-3.5-flash-lite").split(",") if m.strip()]
PUBLIC_MODE = os.environ.get("FAULTLINE_PUBLIC", "").lower() in ("1", "true", "yes")
EDGE_SERVICES = [s.strip() for s in os.environ.get("EDGE_SERVICES", ROOT_SERVICE).split(",") if s.strip()]
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")        # owner/repo: PRs and issues are opened there; empty = written under out/
GITHUB_BASE = os.environ.get("GITHUB_BASE", "faultline")
SLO_P95_MS = float(os.environ["SLO_P95_MS"]) if os.environ.get("SLO_P95_MS") else None
SLO_FAIL_RATE = float(os.environ["SLO_FAIL_RATE"]) if os.environ.get("SLO_FAIL_RATE") else None
