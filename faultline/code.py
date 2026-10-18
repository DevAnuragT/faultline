"""Code pointers: where in the source a finding lives.

Two sources, in order: OpenTelemetry code attributes on the span when the instrumentation emits them
(`code.function.name` / `code.file.path` / `code.line.number`, or the older `code.function` / `code.filepath` / `code.lineno`),
and otherwise a lookup table for the demo app (HotROD lives in the Jaeger repository). The table is explicit so the demo never
claims a line it cannot justify; a real deployment gets the first source for free from its own instrumentation."""
from __future__ import annotations

from . import profile

REPO_URL = profile.REPO_URL
TABLE: dict[tuple[str, str], dict] = {k: {"file": v["file"], "function": v.get("function", ""), "why": v.get("why", "")} for k, v in profile.CODE_MAP.items()}
FLAG_SOURCE = {a["id"].split()[0]: (a["source"]["file"], a["source"].get("symbol", "")) for a in profile.ACTUATORS if a.get("source")}


def pointer(service: str, op: str, span_code: str = "") -> dict | None:
    """Prefer what the span said about itself; fall back to the demo table."""
    if span_code:
        return {"source": "span attributes", "location": span_code}
    hit = TABLE.get((service, op))
    if not hit:
        return None
    return {"source": f"{profile.NAME} source map", "file": hit["file"], "function": hit["function"], "why": hit["why"], "url": f"{REPO_URL}/{hit['file']}" if REPO_URL else ""}


def flag_pointer(flag: str) -> dict | None:
    hit = FLAG_SOURCE.get(flag.split()[0])
    return {"file": hit[0], "symbol": hit[1], "url": f"{REPO_URL}/{hit[0]}" if REPO_URL else ""} if hit else None
