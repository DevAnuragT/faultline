"""Pull a window of traces from Jaeger into FalkorDB: services, operations, spans, critical paths, per-window edges."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from statistics import median

from . import config, jaeger
from .critical import critical_path
from .graph import Store


def _p(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(round(pct / 100 * (len(v) - 1))))]


def ingest_window(st: Store, label: str, traces: list[dict] | None = None, limit: int = 200, lookback: str = "1h",
                  start_us: int | None = None, progress=print) -> dict:
    st.ensure_indexes()
    traces = traces if traces is not None else jaeger.fetch_traces(config.ROOT_SERVICE, limit=limit, lookback=lookback, start_us=start_us)
    if not traces:
        raise RuntimeError("Jaeger returned no traces; run the load generator first")
    wid = f"w-{uuid.uuid4().hex[:8]}"
    starts = [min(s["start_us"] for s in t["spans"]) for t in traces]
    ends = [max(s["start_us"] + s["duration_us"] for s in t["spans"]) for t in traces]
    st.q("CREATE (:Window {id:$id, label:$label, start_us:$s, end_us:$e, traces:$n, created:$ts})",
         {"id": wid, "label": label, "s": min(starts), "e": max(ends), "n": len(traces), "ts": time.time()})
    progress(f"window {wid} ({label}): {len(traces)} traces")

    svc_edges: dict[tuple[str, str], list[dict]] = defaultdict(list)   # (caller svc, callee svc) -> child spans
    op_edges: dict[tuple[str, str], list[dict]] = defaultdict(list)    # (caller op key, callee op key)
    ops: dict[str, tuple[str, str]] = {}
    for t in traces:
        by_id = {s["id"]: s for s in t["spans"]}
        path, self_us = critical_path(t["spans"])
        on_path = set(path)
        root = by_id[path[0]] if path else max(t["spans"], key=lambda s: s["duration_us"])
        errors = sum(1 for s in t["spans"] if s["error"])
        peer = {}  # client span -> the other service it waits on
        for s in t["spans"]:
            p = by_id.get(s["parent"]) if s["parent"] else None
            if p is not None and p["service"] != s["service"]:
                peer.setdefault(p["id"], s["service"])
        rows = []
        for s in t["spans"]:
            key = f"{s['service']}::{s['op']}"
            a = s["attrs"]
            s["proto"] = (f"db:{a['db.system.name']}" if a.get("db.system.name") else "grpc" if a.get("rpc.method") else
                          "http" if a.get("http.request.method") else "")
            ops[key] = (s["service"], s["op"])
            rows.append({"id": s["id"], "parent": s["parent"], "service": s["service"], "op": s["op"], "key": key,
                         "start_us": s["start_us"], "duration_us": s["duration_us"], "kind": s["kind"],
                         "error": s["error"], "status": str(s["status"] or ""), "critical": s["id"] in on_path,
                         "self_us": self_us.get(s["id"], 0),
                         # the service a client span waits on: its server child, or the address it dialled when nobody answered
                         "peer": peer.get(s["id"]) or (s["attrs"].get("server.address", "") if s["kind"] == "client" else ""), "proto": s["proto"],
                         "code": s.get("code", "")})
            p = by_id.get(s["parent"]) if s["parent"] else None
            if p is not None and p["service"] != s["service"]:
                svc_edges[(p["service"], s["service"])].append(s)
            if p is not None:
                op_edges[(f"{p['service']}::{p['op']}", key)].append(s)
        st.q("""MATCH (w:Window {id:$w})
                CREATE (t:Trace {id:$id, window:$w, root:$root, root_service:$rs, duration_ms:$d, status:$status, errors:$errors,
                                 start_us:$start})-[:IN]->(w)
                WITH t UNWIND $rows AS r
                MERGE (o:Operation {key:r.key}) ON CREATE SET o.name = r.op, o.service = r.service
                MERGE (s:Service {name:r.service})
                MERGE (s)-[:EXPOSES]->(o)
                CREATE (sp:Span {id:r.id, trace:$id, window:$w, service:r.service, op:r.op, start_us:r.start_us,
                                 duration_us:r.duration_us, kind:r.kind, error:r.error, status:r.status,
                                 critical:r.critical, self_us:r.self_us, parent:r.parent, peer:r.peer, proto:r.proto, code:r.code})-[:OF]->(t)
                CREATE (sp)-[:ON]->(o)""",
             {"w": wid, "id": t["id"], "root": root["op"], "rs": root["service"], "d": root["duration_us"] / 1000,
              "status": str(root["status"] or ""), "errors": errors, "start": root["start_us"], "rows": rows})
    st.q("MATCH (c:Span {window:$w})-[:OF]->(t:Trace) MATCH (p:Span {id:c.parent, trace:t.id}) CREATE (c)-[:CHILD_OF]->(p)", {"w": wid})

    def stats(spans: list[dict]) -> dict:
        d = [s["duration_us"] / 1000 for s in spans]
        protos = [s.get("proto") for s in spans if s.get("proto")]
        return {"count": len(spans), "errors": sum(1 for s in spans if s["error"]), "p50_ms": median(d), "p95_ms": _p(d, 95),
                "proto": max(set(protos), key=protos.count) if protos else ""}

    for (a, b), spans in svc_edges.items():
        st.q("MATCH (a:Service {name:$a}), (b:Service {name:$b}) CREATE (a)-[:CALLS {window:$w, count:$count, errors:$errors, "
             "p50_ms:$p50_ms, p95_ms:$p95_ms, per_trace:$pt, proto:$proto}]->(b)",
             {"a": a, "b": b, "w": wid, **stats(spans), "pt": len(spans) / len(traces)})
    for (a, b), spans in op_edges.items():
        st.q("MATCH (a:Operation {key:$a}), (b:Operation {key:$b}) CREATE (a)-[:INVOKES {window:$w, count:$count, errors:$errors, "
             "p50_ms:$p50_ms, p95_ms:$p95_ms, per_trace:$pt, proto:$proto}]->(b)",
             {"a": a, "b": b, "w": wid, **stats(spans), "pt": len(spans) / len(traces)})
    progress(f"  {len(ops)} operations, {len(svc_edges)} service edges, {len(op_edges)} operation edges")
    return {"window": wid, "label": label, "traces": len(traces), "operations": len(ops), "service_edges": len(svc_edges)}


def windows(st: Store) -> list[dict]:
    _, rows = st.ro("MATCH (w:Window) RETURN w.id, w.label, w.traces, w.created ORDER BY w.created")
    return [{"id": r[0], "label": r[1], "traces": r[2], "created": r[3]} for r in rows]


def latest_window(st: Store, label: str | None = None, steady: bool = True) -> str:
    """Newest window. By default only steady-state windows count (baseline / fix-*), never a fault experiment's window:
    those are what the system looked like while something was deliberately broken."""
    if label:
        rows = st.q("MATCH (w:Window {label:$l}) RETURN w.id ORDER BY w.created DESC LIMIT 1", {"l": label})
    elif steady:
        rows = st.q("MATCH (w:Window) WHERE NOT w.label STARTS WITH 'pause-' RETURN w.id ORDER BY w.created DESC LIMIT 1")
    else:
        rows = st.q("MATCH (w:Window) RETURN w.id ORDER BY w.created DESC LIMIT 1")
    if not rows:
        raise RuntimeError("no ingested window")
    return rows[0][0]
