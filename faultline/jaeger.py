"""Read traces from the Jaeger query API and normalise them into plain dicts."""
from __future__ import annotations

import time

import requests

from . import profile


def fetch_traces(service: str, limit: int = 200, lookback: str = "1h", start_us: int | None = None) -> list[dict]:
    """Traces whose root is `service`; `start_us` (epoch microseconds) bounds the window instead of `lookback`."""
    params: dict = {"service": service, "limit": limit}
    if start_us:
        params.update(start=int(start_us), end=int(time.time() * 1_000_000))
    else:
        params["lookback"] = lookback
    r = requests.get(f"{profile.JAEGER_URL}/api/traces", params=params, timeout=30)
    r.raise_for_status()
    return [normalise(t) for t in r.json()["data"]]


def list_services() -> list[str]:
    r = requests.get(f"{profile.JAEGER_URL}/api/services", timeout=30)
    r.raise_for_status()
    return [s for s in (r.json().get("data") or []) if s != "jaeger-all-in-one"]


def fetch_root_traces(limit: int = 200, lookback: str = "1h", start_us: int | None = None) -> list[dict]:
    """Traces for every root service the profile names, or for every service Jaeger knows when it names none; de-duplicated."""
    roots = profile.ROOT_SERVICES or list_services()
    seen, out = set(), []
    for svc in roots:
        for t in fetch_traces(svc, limit=limit, lookback=lookback, start_us=start_us):
            if t["id"] not in seen:
                seen.add(t["id"]); out.append(t)
    return out


def _tags(span: dict) -> dict:
    return {t["key"]: t["value"] for t in span.get("tags", [])}


def _code(tags: dict) -> str:
    """OpenTelemetry code attributes, new names first, then the pre-1.26 names. Empty when the instrumentation emits none."""
    fn = tags.get("code.function.name") or tags.get("code.function") or ""
    path = tags.get("code.file.path") or tags.get("code.filepath") or ""
    line = tags.get("code.line.number") or tags.get("code.lineno") or ""
    if not (fn or path):
        return ""
    return f"{path}{':' + str(line) if line else ''}{' ' + fn if fn else ''}".strip()


def normalise(trace: dict) -> dict:
    """One trace -> {id, spans:[{id, parent, service, op, start_us, duration_us, kind, error, status, attrs}]}."""
    procs = {k: v["serviceName"] for k, v in trace["processes"].items()}
    spans = []
    for s in trace["spans"]:
        tags = _tags(s)
        parent = next((r["spanID"] for r in s.get("references", []) if r["refType"] == "CHILD_OF"), None)
        error = tags.get("otel.status_code") == "ERROR" or tags.get("error") is True
        spans.append({
            "id": s["spanID"], "parent": parent, "service": procs[s["processID"]], "op": s["operationName"],
            "start_us": s["startTime"], "duration_us": max(s["duration"], 0), "kind": tags.get("span.kind", "internal"),
            "error": bool(error), "status": tags.get("http.response.status_code") or tags.get("rpc.response.status_code"),
            "attrs": {k: tags[k] for k in ("http.route", "http.request.method", "rpc.method", "db.system.name",
                                            "db.operation.name", "server.address", "server.port") if k in tags},
            "code": _code(tags),
        })
    return {"id": trace["traceID"], "spans": spans}
