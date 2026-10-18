"""The profile describes ONE system: where its traces are, how to generate load (or not), how Faultline may touch its
services (compose, kubernetes, or not at all), which remedies map to which changes, and where its code lives.
Everything HotROD-specific lives in profiles/hotrod.yaml; your own system gets its own file."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from . import config

DEFAULT = config.ROOT / "profiles" / "hotrod.yaml"


def _find() -> Path:
    env = os.environ.get("FAULTLINE_PROFILE")
    if env:
        return Path(env).expanduser()
    for cand in (Path.cwd() / "faultline.yaml", config.ROOT / "faultline.yaml"):
        if cand.exists():
            return cand
    return DEFAULT


PATH = _find()
RAW: dict = yaml.safe_load(PATH.read_text()) if PATH.exists() else {}
NAME = RAW.get("name", PATH.stem)
_dir = PATH.parent

# --- traces
_tr = RAW.get("traces", {})
JAEGER_URL = os.environ.get("JAEGER_URL") or _tr.get("jaeger_url", "http://localhost:16686")
# where traces come from: "jaeger" (query API), "otlp" (Faultline's own OTLP/HTTP endpoint, POST /v1/traces), "file" (an export)
SOURCE = os.environ.get("TRACE_SOURCE") or _tr.get("source") or ("jaeger" if _tr.get("jaeger_url") else "otlp")
TRACE_FILE = _tr.get("file", "")
ROOT_SERVICES: list[str] = list(_tr.get("root_services") or [])
EDGE_SERVICES: list[str] = list(_tr.get("edge_services") or ROOT_SERVICES)

# --- load
LOAD = RAW.get("load") or None  # None = observe-only
LOAD_URLS: list[str] = list((LOAD or {}).get("urls") or [])

# --- environment
_env = RAW.get("environment", {})
BACKEND = _env.get("backend", "none")
_cf = _env.get("compose_file", "docker-compose.yml")
COMPOSE_FILE = Path(_cf) if Path(_cf).is_absolute() else (_dir / _cf if (_dir / _cf).exists() else config.ROOT / _cf)
OVERRIDE_FILE = COMPOSE_FILE.with_name(COMPOSE_FILE.stem + ".fix.yml")
K8S = _env.get("kubernetes", {}) or {}

# --- services
SERVICES: dict[str, dict] = RAW.get("services", {}) or {}
PAUSABLE: list[str] = [s for s, v in SERVICES.items() if v.get("compose") or v.get("kubernetes") or v.get("deployment")]
IN_PROCESS_OF: dict[str, str] = {s: v["in_process_of"] for s, v in SERVICES.items() if v.get("in_process_of")}


def runtime_name(service: str) -> str:
    """The compose service / kubernetes deployment behind a trace service name."""
    v = SERVICES.get(service, {})
    return v.get("compose") or v.get("deployment") or v.get("kubernetes") or service


# --- actuators
ACTUATORS: list[dict] = RAW.get("actuators", []) or []
ACTUATOR_BY_ID: dict[str, dict] = {a["id"]: a for a in ACTUATORS}


def actuator_for(kind: str, service: str) -> dict | None:
    owner = IN_PROCESS_OF.get(service, service)
    for a in ACTUATORS:
        if a.get("kind") == kind and a.get("service") in (service, owner):
            return a
    return None


# --- code
_code = RAW.get("code", {}) or {}
SOURCE_ROOT = os.environ.get("SOURCE_ROOT") or _code.get("source_root", "") or ""
RAW_BASE = os.environ.get("SOURCE_RAW_BASE") or _code.get("raw_base", "") or ""
REPO_URL = _code.get("repo_url", "") or ""
CODE_MAP: dict[tuple[str, str], dict] = {(m["service"], m["op"]): m for m in (_code.get("map") or [])}

# --- slo
SLO = RAW.get("slo") or {}
