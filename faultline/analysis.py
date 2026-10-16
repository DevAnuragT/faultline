"""Graph questions: who owns the latency, who is a single point of failure, what amplifies, what would break."""
from __future__ import annotations

from .graph import Store


HANG_FACTOR = 10.0  # a trace this many times slower than its operation's median is a hang, not a slow request


def critical_owners(st: Store, window: str, root_op: str | None = None, include_hangs: bool = False) -> list[dict]:
    """Share of end-to-end latency owned by each (service, operation, peer) on the critical path (CRISP bottom-up).
    Hung traces are excluded by default so one 30 s wait does not drown the steady-state picture."""
    _, rows = st.ro("""
        MATCH (t:Trace {window:$w}) WHERE $root IS NULL OR t.root = $root
        WITH t.root AS root, percentileDisc(t.duration_ms, 0.5) AS med, collect(t) AS ts
        UNWIND ts AS t WITH t WHERE $hangs OR t.duration_ms <= $f * med
        WITH collect(t) AS ts, sum(t.duration_ms) AS total, count(t) AS n
        UNWIND ts AS t
        MATCH (s:Span {critical:true})-[:OF]->(t)
        WITH s.service AS service, s.op AS op, s.peer AS peer, sum(s.self_us)/1000.0 AS owned_ms, count(s) AS spans, total, n,
             sum(CASE WHEN s.error THEN 1 ELSE 0 END) AS errors
        RETURN service, op, peer, owned_ms, 100.0*owned_ms/total AS share, spans, errors, owned_ms/n ORDER BY owned_ms DESC""",
        {"w": window, "root": root_op, "hangs": include_hangs, "f": HANG_FACTOR})
    return [{"service": r[0], "op": r[1], "peer": r[2] or "", "owned_ms": round(r[3], 1), "share_pct": round(r[4], 1), "spans": r[5],
             "errors": r[6], "per_request_ms": round(r[7], 1)} for r in rows]


def label(o: dict) -> str:
    return f"{o['service']} {o['op']}" + (f" -> {o['peer']}" if o.get("peer") else "")


def service_graph(st: Store, window: str) -> list[dict]:
    _, rows = st.ro("MATCH (a:Service)-[c:CALLS {window:$w}]->(b:Service) RETURN a.name, b.name, c.count, c.errors, c.p50_ms, c.p95_ms, c.per_trace",
                    {"w": window})
    return [{"from": r[0], "to": r[1], "count": r[2], "errors": r[3], "p50_ms": round(r[4], 1), "p95_ms": round(r[5], 1),
             "per_trace": round(r[6], 1)} for r in rows]


def single_points_of_failure(st: Store, window: str) -> list[dict]:
    """Betweenness on the service graph of this window, with the traffic that transits each service."""
    rows = st.q("""
        CALL algo.betweenness({nodeLabels:['Service'], relationshipTypes:['CALLS']}) YIELD node, score
        OPTIONAL MATCH (node)<-[c:CALLS {window:$w}]-()
        OPTIONAL MATCH ()<-[:CALLS {window:$w}]-(node)
        RETURN node.name, score, sum(c.count), sum(c.errors) ORDER BY score DESC, sum(c.count) DESC""", {"w": window})
    return [{"service": r[0], "betweenness": r[1], "inbound_calls": r[2] or 0, "inbound_errors": r[3] or 0} for r in rows]


def fan_out(st: Store, window: str, threshold: float = 3.0) -> list[dict]:
    """Hops where one request turns into many calls: latency multipliers and load amplifiers."""
    _, rows = st.ro("""
        MATCH (a:Service)-[c:CALLS {window:$w}]->(b:Service) WHERE c.per_trace >= $th
        RETURN a.name, b.name, c.per_trace, c.count, c.errors, c.p95_ms ORDER BY c.per_trace DESC""", {"w": window, "th": threshold})
    return [{"from": r[0], "to": r[1], "calls_per_request": round(r[2], 1), "count": r[3], "errors": r[4], "p95_ms": round(r[5], 1)} for r in rows]


def blast_radius(st: Store, window: str, service: str) -> dict:
    """What the graph predicts breaks if `service` stops: every root operation with a call path into it."""
    _, rows = st.ro("""
        MATCH (t:Service {name:$s})
        MATCH path = (r:Service)-[:CALLS* {window:$w}]->(t) WHERE NOT ()-[:CALLS {window:$w}]->(r)
        WITH r, min(length(path)) AS hops
        MATCH (r)-[:EXPOSES]->(o:Operation)<-[:ON]-(sp:Span {window:$w}) WHERE sp.parent IS NULL
        RETURN r.name, o.name, hops, count(sp) ORDER BY count(sp) DESC""", {"w": window, "s": service})
    roots = [{"service": r[0], "op": r[1], "hops": r[2], "traces": r[3]} for r in rows]
    _, deps = st.ro("MATCH (a:Service)-[:CALLS* {window:$w}]->(t:Service {name:$s}) RETURN DISTINCT a.name", {"w": window, "s": service})
    return {"target": service, "root_operations": roots, "dependent_services": sorted(d[0] for d in deps)}


def hangs(st: Store, window: str, factor: float = HANG_FACTOR) -> list[dict]:
    """Root traces far slower than their operation's median: a dependency hung and nothing failed fast."""
    _, rows = st.ro("""
        MATCH (t:Trace {window:$w})
        WITH t.root AS root, percentileDisc(t.duration_ms, 0.5) AS med, collect(t) AS ts
        UNWIND ts AS t WITH root, med, t WHERE t.duration_ms > $f * med
        MATCH (s:Span {critical:true})-[:OF]->(t) WITH root, med, t, s ORDER BY s.self_us DESC
        WITH root, med, t, collect({service:s.service, op:s.op, peer:s.peer, self_ms:s.self_us/1000.0, error:s.error})[0] AS worst
        RETURN root, med, t.id, t.duration_ms, t.status, worst ORDER BY t.duration_ms DESC""", {"w": window, "f": factor})
    return [{"root": r[0], "median_ms": round(r[1], 1), "trace": r[2], "duration_ms": round(r[3], 1), "status": r[4], "worst": r[5]} for r in rows]


def root_health(st: Store, window: str, factor: float = HANG_FACTOR, baseline_p50: dict[str, float] | None = None) -> list[dict]:
    """Per root operation: traces, failures (non-2xx, or hung = slower than `factor` x the median), p50/p95.
    Pass the baseline window's medians when judging an experiment, so a window where everything hung still counts as failed."""
    _, rows = st.ro("""
        MATCH (t:Trace {window:$w})
        WITH t.root AS root, t.root_service AS service, collect(t) AS ts, percentileDisc(t.duration_ms, 0.5) AS p50,
             percentileDisc(t.duration_ms, 0.95) AS p95
        WITH root, service, ts, p50, p95, coalesce($base[root], p50) AS ref
        RETURN root, service, size(ts), size([t IN ts WHERE NOT t.status STARTS WITH '2' OR t.duration_ms > $f * ref]), p50, p95
        ORDER BY size(ts) DESC""", {"w": window, "f": factor, "base": baseline_p50 or {}})
    return [{"op": r[0], "service": r[1], "traces": r[2], "failed": r[3], "fail_rate": round(r[3] / r[2], 3) if r[2] else 0.0,
             "p50_ms": round(r[4], 1), "p95_ms": round(r[5], 1)} for r in rows]


def retries(st: Store, window: str) -> list[dict]:
    """Silent retries: a child span of the same operation started after an errored sibling under the same parent."""
    _, rows = st.ro("""
        MATCH (c1:Span {window:$w, error:true})-[:CHILD_OF]->(p:Span)<-[:CHILD_OF]-(c2:Span)
        WHERE c1.op = c2.op AND c1.service = c2.service AND c2.start_us > c1.start_us
        WITH c1, p, min(c2.start_us) AS nxt
        MATCH (c2:Span {trace:c1.trace, start_us:nxt, op:c1.op})-[:CHILD_OF]->(p)
        WITH c1.service AS service, c1.op AS op, count(DISTINCT c1) AS retried_errors, sum(c1.duration_us)/1000.0 AS wasted_ms,
             count(DISTINCT c1.trace) AS traces, sum(CASE WHEN c2.critical THEN 1 ELSE 0 END) AS on_critical
        RETURN service, op, retried_errors, wasted_ms, traces, on_critical ORDER BY wasted_ms DESC""", {"w": window})
    _, n = st.ro("MATCH (t:Trace {window:$w}) RETURN count(t)", {"w": window})
    total = n[0][0] or 1
    return [{"service": r[0], "op": r[1], "retried_errors": r[2], "wasted_ms_per_request": round(r[3] / total, 1), "traces": r[4],
             "retries_on_critical_path": r[5]} for r in rows]


def critical_chain(st: Store, window: str, root_op: str | None = None) -> list[dict]:
    """Top-down: the critical path of the slowest steady-state trace, as an ordered chain of (service, op, self ms)."""
    _, rows = st.ro("""
        MATCH (t:Trace {window:$w}) WHERE $root IS NULL OR t.root = $root
        WITH t.root AS root, percentileDisc(t.duration_ms, 0.5) AS med, collect(t) AS ts
        UNWIND ts AS t WITH t WHERE t.duration_ms <= $f * med
        WITH t ORDER BY t.duration_ms DESC LIMIT 1
        MATCH (s:Span {critical:true})-[:OF]->(t)
        RETURN t.id, t.duration_ms, s.service, s.op, s.peer, s.start_us, s.duration_us, s.self_us, s.error
        ORDER BY s.start_us""", {"w": window, "root": root_op, "f": HANG_FACTOR})
    return [{"trace": r[0], "trace_ms": round(r[1], 1), "service": r[2], "op": r[3], "peer": r[4] or "", "offset_ms": 0.0,
             "duration_ms": round(r[6] / 1000, 1), "self_ms": round(r[7] / 1000, 1), "error": r[8]} for r in rows] if rows else []


def findings(st: Store, window: str) -> list[dict]:
    out: list[dict] = []
    for o in critical_owners(st, window)[:3]:
        if o["share_pct"] >= 15:
            out.append({"kind": "critical_owner", "severity": "high" if o["share_pct"] >= 30 else "medium", "service": o["service"],
                        "detail": f"{label(o)} owns {o['share_pct']}% of steady-state end-to-end latency on the critical path "
                                  f"({o['per_request_ms']:.0f} ms per request, {o['spans']} critical spans)"})
    for f in fan_out(st, window):
        out.append({"kind": "fan_out", "severity": "medium", "service": f["from"],
                    "detail": f"{f['from']} -> {f['to']}: {f['calls_per_request']} calls per request "
                              f"({f['errors']} errors, p95 {f['p95_ms']} ms); amplifies both latency and load"})
    grouped: dict[tuple, list[dict]] = {}
    for h in hangs(st, window):
        grouped.setdefault((h["root"], h["worst"]["service"], h["worst"].get("peer", "")), []).append(h)
    for (root, svc, peer), hs in grouped.items():
        worst = max(hs, key=lambda h: h["duration_ms"]); op = worst["worst"]["op"]
        out.append({"kind": "hang_not_fail", "severity": "high", "service": svc,
                    "detail": f"{len(hs)} request(s) to {root} hung for up to {worst['duration_ms']/1000:.1f} s (median {worst['median_ms']:.0f} ms) "
                              f"and still returned {worst['status'] or 'no status'}: the time went waiting in {label({'service': svc, 'op': op, 'peer': peer})}; "
                              f"no timeout, no fail-fast"})
    for r in retries(st, window):
        if r["retried_errors"] >= 3:
            out.append({"kind": "silent_retry", "severity": "medium", "service": r["service"],
                        "detail": f"{r['service']} {r['op']}: {r['retried_errors']} failed calls were retried in {r['traces']} requests, "
                                  f"{r['wasted_ms_per_request']} ms per request spent on the failed attempt; {r['retries_on_critical_path']} retries land on the critical path"})
    centrality = {s["service"]: s for s in single_points_of_failure(st, window)}
    _, svcs = st.ro("MATCH (s:Service) WHERE ()-[:CALLS {window:$w}]->(s) RETURN s.name", {"w": window})
    transit, leaves, roots_hit, total = [], [], set(), 0
    for (name,) in svcs:
        br = blast_radius(st, window, name)
        if not br["root_operations"]:
            continue
        roots_hit |= {r["op"] for r in br["root_operations"]}
        total = max(total, sum(r["traces"] for r in br["root_operations"]))
        (transit if centrality.get(name, {}).get("betweenness", 0) > 0 else leaves).append(name)
    if transit or leaves:
        out.append({"kind": "single_point_of_failure", "severity": "high", "service": (transit or leaves)[0],
                    "detail": f"no redundancy anywhere: every recorded path to {', '.join(sorted(roots_hit))} runs through "
                              f"{', '.join(transit)} (transit, betweenness > 0) and {', '.join(leaves)} (leaves); the graph predicts stopping "
                              f"any one of them fails {total}/{total} requests", "services": transit + leaves})
    return out


def record_findings(st: Store, window: str, items: list[dict]) -> int:
    st.q("MATCH (f:Finding {window:$w}) DETACH DELETE f", {"w": window})
    for i, f in enumerate(items):
        st.q("""MATCH (s:Service {name:$svc}) CREATE (f:Finding {id:$id, window:$w, kind:$kind, severity:$sev, service:$svc, detail:$detail})
                CREATE (f)-[:ABOUT]->(s)""", {"id": f"{window}-f{i}", "w": window, **{"kind": f["kind"], "sev": f["severity"], "svc": f["service"], "detail": f["detail"]}})
    return len(items)
