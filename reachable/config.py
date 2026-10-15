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


SECRETS_INFO: dict = {"keys": [], "error": None}


def _flatten(d, out: dict) -> None:
    for k, v in d.items():
        if hasattr(v, "items"):
            _flatten(v, out)
        elif isinstance(v, (str, int, float, bool)):
            out[str(k).upper()] = str(v).strip()


def _load_streamlit_secrets() -> None:
    """On Streamlit Cloud, read secrets directly (don't rely on them being exported as env vars)."""
    try:
        import streamlit as st

        flat: dict = {}
        _flatten(st.secrets, flat)
        SECRETS_INFO["keys"] = sorted(flat)
        for k, v in flat.items():
            os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass  # no secrets configured (local run)
    except Exception as exc:  # e.g. malformed TOML: surface the type, never the content
        SECRETS_INFO["error"] = exc.__class__.__name__


_load_dotenv()
_load_streamlit_secrets()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3-flash-preview").split(",") if m.strip()]
FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.environ.get("FALKORDB_PASSWORD") or None
FALKORDB_USERNAME = os.environ.get("FALKORDB_USERNAME") or None
FALKORDB_SSL = os.environ.get("FALKORDB_SSL", "").lower() in ("1", "true", "yes")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GRAPH_NAME = os.environ.get("GRAPH_NAME", "reachable")

for d in (CACHE, SRC_CACHE):
    d.mkdir(parents=True, exist_ok=True)
