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
