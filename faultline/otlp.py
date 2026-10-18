"""OTLP traces in, with no Jaeger in between.

Any OpenTelemetry SDK or Collector can export OTLP/HTTP to Faultline's own endpoint (`POST /v1/traces`, protobuf or JSON,
gzip accepted). Spans are buffered per trace in memory and appended to a JSONL spool so a window can be ingested later by
time range, exactly like the Jaeger source. The same module also imports files: OTLP JSON exports and Jaeger JSON exports."""
from __future__ import annotations

import gzip
import json
import threading
import time
from pathlib import Path

from . import config

SPOOL = config.ROOT / "data" / "otlp" / "spans.jsonl"
KINDS = {0: "internal", 1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer",
         "SPAN_KIND_UNSPECIFIED": "internal", "SPAN_KIND_INTERNAL": "internal", "SPAN_KIND_SERVER": "server",
         "SPAN_KIND_CLIENT": "client", "SPAN_KIND_PRODUCER": "producer", "SPAN_KIND_CONSUMER": "consumer"}
_lock = threading.Lock()
_buffer: dict[str, list[dict]] = {}   # trace id -> normalized spans
_seen_at: dict[str, float] = {}
_received = 0


# ----------------------------------------------------------------------------------------------- attribute helpers
def _any(v: dict):
    """OTLP JSON AnyValue -> python value."""
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_any(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _any(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])}
    return None


def _attrs_json(items: list[dict]) -> dict:
    return {kv["key"]: _any(kv.get("value", {})) for kv in items or []}


def _attrs_pb(items) -> dict:
    out = {}
    for kv in items:
        v = kv.value
        which = v.WhichOneof("value")
        out[kv.key] = (int(v.int_value) if which == "int_value" else v.string_value if which == "string_value" else v.bool_value if which == "bool_value"
                       else v.double_value if which == "double_value" else None)
    return out


def _hex(b) -> str:
    return b.hex() if isinstance(b, (bytes, bytearray)) else (b or "")


def _norm(trace_id: str, span_id: str, parent: str, service: str, name: str, start_ns: int, end_ns: int, kind, status_code, attrs: dict) -> dict:
    """One OTLP span -> Faultline's span shape (the same one jaeger.normalise produces)."""
    error = status_code in (2, "STATUS_CODE_ERROR", "ERROR")
    http = attrs.get("http.response.status_code") or attrs.get("http.status_code")
    rpc = attrs.get("rpc.grpc.status_code")
    fn = attrs.get("code.function.name") or attrs.get("code.function") or ""
    path = attrs.get("code.file.path") or attrs.get("code.filepath") or ""
    line = attrs.get("code.line.number") or attrs.get("code.lineno") or ""
    code = f"{path}{':' + str(line) if line else ''}{' ' + fn if fn else ''}".strip() if (fn or path) else ""
    return {"id": span_id, "parent": parent or None, "service": service, "op": name, "start_us": int(start_ns) // 1000,
            "duration_us": max(int(end_ns) - int(start_ns), 0) // 1000, "kind": KINDS.get(kind, "internal"), "error": bool(error),
            "status": str(http) if http is not None else (str(rpc) if rpc is not None else None),
            "attrs": {k: attrs[k] for k in ("http.route", "http.request.method", "rpc.method", "db.system.name", "db.system",
                                           "db.operation.name", "server.address", "server.port", "peer.service") if k in attrs},
            "code": code, "trace": trace_id}


def spans_from_json(doc: dict) -> list[dict]:
    out = []
    for rs in doc.get("resourceSpans", []):
        res = _attrs_json(rs.get("resource", {}).get("attributes", []))
        service = str(res.get("service.name") or "unknown")
        for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])):
            for sp in ss.get("spans", []):
                out.append(_norm(sp.get("traceId", ""), sp.get("spanId", ""), sp.get("parentSpanId", ""), service, sp.get("name", ""),
                                 int(sp.get("startTimeUnixNano", 0)), int(sp.get("endTimeUnixNano", 0)), sp.get("kind", 0),
                                 (sp.get("status") or {}).get("code"), _attrs_json(sp.get("attributes", []))))
    return out


def spans_from_protobuf(body: bytes) -> list[dict]:
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
    req = trace_service_pb2.ExportTraceServiceRequest(); req.ParseFromString(body)
    out = []
    for rs in req.resource_spans:
        res = _attrs_pb(rs.resource.attributes)
        service = str(res.get("service.name") or "unknown")
        for ss in rs.scope_spans:
            for sp in ss.spans:
                out.append(_norm(_hex(sp.trace_id), _hex(sp.span_id), _hex(sp.parent_span_id), service, sp.name, sp.start_time_unix_nano,
                                 sp.end_time_unix_nano, sp.kind, sp.status.code, _attrs_pb(sp.attributes)))
    return out


# ----------------------------------------------------------------------------------------------- receiving
def receive(body: bytes, content_type: str = "", content_encoding: str = "") -> dict:
    """Accept one OTLP/HTTP export request. Returns the OTLP success response shape."""
    global _received
    if "gzip" in (content_encoding or ""):
        body = gzip.decompress(body)
    spans = spans_from_json(json.loads(body.decode())) if "json" in (content_type or "") or body[:1] in (b"{", b" ") else spans_from_protobuf(body)
    add(spans)
    _received += len(spans)
    return {"partialSuccess": {}}


def add(spans: list[dict], spool: bool = True) -> None:
    now = time.time()
    with _lock:
        for s in spans:
            _buffer.setdefault(s["trace"], []).append(s)
            _seen_at[s["trace"]] = now
        if spool and spans:
            SPOOL.parent.mkdir(parents=True, exist_ok=True)
            with SPOOL.open("a") as f:
                for s in spans:
                    f.write(json.dumps(s, separators=(",", ":")) + "\n")


def stats() -> dict:
    with _lock:
        return {"traces_buffered": len(_buffer), "spans_received": _received, "spool": str(SPOOL) if SPOOL.exists() else None}


def traces(start_us: int | None = None, end_us: int | None = None, lookback_s: float | None = None, settle_s: float = 3.0, limit: int = 500) -> list[dict]:
    """Complete traces (a root span present, no spans arriving for `settle_s`) whose root started in the time range."""
    _load_spool_once()
    now = time.time()
    lo = start_us if start_us is not None else (int((now - lookback_s) * 1_000_000) if lookback_s else None)
    out = []
    with _lock:
        for tid, spans in _buffer.items():
            if now - _seen_at.get(tid, 0) < settle_s:
                continue
            ids = {s["id"] for s in spans}
            roots = [s for s in spans if not s["parent"] or s["parent"] not in ids]
            if not roots:
                continue
            root = max(roots, key=lambda s: s["duration_us"])
            if lo is not None and root["start_us"] < lo:
                continue
            if end_us is not None and root["start_us"] > end_us:
                continue
            out.append({"id": tid, "spans": [dict(s, parent=s["parent"] if s["parent"] in ids else None) for s in spans]})
    out.sort(key=lambda t: max(s["start_us"] for s in t["spans"]), reverse=True)
    return out[:limit]


_spool_loaded = False


def _load_spool_once() -> None:
    """Spans received by a previous process (the standalone receiver) are picked up from the spool."""
    global _spool_loaded
    if _spool_loaded or not SPOOL.exists():
        _spool_loaded = True; return
    spans = [json.loads(l) for l in SPOOL.read_text().splitlines() if l.strip()]
    _spool_loaded = True
    add(spans, spool=False)
    with _lock:
        for tid in _buffer:
            _seen_at[tid] = 0.0  # old spool is settled


# ----------------------------------------------------------------------------------------------- files
def traces_from_file(path: str) -> list[dict]:
    """Import an OTLP JSON export (one object, or JSON lines of ExportTraceServiceRequest) or a Jaeger JSON export ({"data": [...]})."""
    text = Path(path).read_text()
    from . import jaeger
    try:
        doc = json.loads(text)
        docs = [doc]
    except json.JSONDecodeError:  # JSON lines
        docs = [json.loads(l) for l in text.splitlines() if l.strip()]
    spans, jaeger_traces = [], []
    for d in docs:
        if "resourceSpans" in d:
            spans += spans_from_json(d)
        elif "data" in d:
            jaeger_traces += [jaeger.normalise(t) for t in d["data"]]
        elif "spans" in d and "processes" in d:
            jaeger_traces.append(jaeger.normalise(d))
    by: dict[str, list[dict]] = {}
    for s in spans:
        by.setdefault(s["trace"], []).append(s)
    out = []
    for tid, ss in by.items():
        ids = {s["id"] for s in ss}
        out.append({"id": tid, "spans": [dict(s, parent=s["parent"] if s["parent"] in ids else None) for s in ss]})
    return out + jaeger_traces
