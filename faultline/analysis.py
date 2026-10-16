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
             sum(CASE WHEN s.error THEN 1 ELSE 0 END) AS errors, head(collect(s.code)) AS code
        RETURN service, op, peer, owned_ms, 100.0*owned_ms/total AS share, spans, errors, owned_ms/n, code ORDER BY owned_ms DESC""",
        {"w": window, "root": root_op, "hangs": include_hangs, "f": HANG_FACTOR})
    from . import code
    return [{"service": r[0], "op": r[1], "peer": r[2] or "", "owned_ms": round(r[3], 1), "share_pct": round(r[4], 1), "spans": r[5],
             "errors": r[6], "per_request_ms": round(r[7], 1), "code": code.pointer(r[0], r[1], r[8] or "")} for r in rows]


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


def exposure(st: Store, window: str) -> list[dict]:
    """Security view of the same graph: every data store, the shortest call path from an edge service into it, the
    protocol of each hop as recorded in the spans, and the amplification on the last hop. Facts only: a hop is 'http'
    or 'grpc' as instrumented; whether it is encrypted is not in the traces, so it is reported as 'no TLS attribute'."""
    from . import config
    _, rows = st.ro("""
        MATCH (edge:Service) WHERE edge.name IN $edges
        MATCH (store:Service)<-[c:CALLS {window:$w}]-() WHERE c.proto STARTS WITH 'db:'
        WITH DISTINCT edge, store
        MATCH p = (edge)-[:CALLS*1..6 {window:$w}]->(store)
        WITH edge, store, p ORDER BY length(p)
        WITH edge, store, collect(p)[0] AS p
        RETURN edge.name, store.name, [n IN nodes(p) | n.name], [r IN relationships(p) | r.proto], [r IN relationships(p) | r.per_trace]""",
        {"edges": config.EDGE_SERVICES, "w": window})
    out = []
    for edge, store, nodes, protos, fan in rows:
        last = protos[-1] if protos else ""
        out.append({"edge": edge, "store": store, "path": " -> ".join(nodes), "hops": len(nodes) - 1, "protocols": protos,
                    "store_kind": last.replace("db:", ""), "calls_per_request_into_store": round(fan[-1], 1) if fan else 0.0,
                    "notes": [f"{nodes[i]} -> {nodes[i + 1]} over {pr or 'unknown'}: no TLS attribute recorded" for i, pr in enumerate(protos) if pr in ("http", "grpc")]})
    return sorted(out, key=lambda r: (r["hops"], r["store"]))


def trace_waterfall(st: Store, trace_id: str) -> dict:
    """Every span of one trace with depth and offset: the waterfall view, critical spans marked."""
    _, rows = st.ro("""MATCH (s:Span)-[:OF]->(t:Trace {id:$id})
                       RETURN t.duration_ms, t.root, s.id, s.parent, s.service, s.op, s.peer, s.start_us, s.duration_us, s.critical, s.self_us, s.error, s.kind
                       ORDER BY s.start_us""", {"id": trace_id})
    if not rows:
        return {"trace": trace_id, "spans": []}
    t0 = min(r[7] for r in rows)
    depth: dict[str, int] = {}
    by_id = {r[2]: r for r in rows}

    def d(sid: str) -> int:
        if sid in depth:
            return depth[sid]
        parent = by_id[sid][3]
        depth[sid] = 0 if parent not in by_id else d(parent) + 1
        return depth[sid]

    spans = [{"id": r[2], "parent": r[3], "service": r[4], "op": r[5], "peer": r[6] or "", "offset_ms": round((r[7] - t0) / 1000, 2),
              "duration_ms": round(r[8] / 1000, 2), "critical": bool(r[9]), "self_ms": round((r[10] or 0) / 1000, 2), "error": bool(r[11]),
              "kind": r[12], "depth": d(r[2])} for r in rows]
    return {"trace": trace_id, "root": rows[0][1], "duration_ms": round(rows[0][0], 2), "spans": spans}


def incident(st: Store, window: str, baseline: str | None = None) -> dict | None:
    """Is this window an incident against the steady state? Failure rate up, or p95 more than 1.5x the baseline.
    Returns the write-up material: endpoint, numbers, the owner that moved, the worst trace, the hop to look at."""
    if baseline is None:  # the steady-state window that preceded this one; the first window has nothing to regress from
        rows = st.q("""MATCH (w:Window {id:$w}) MATCH (b:Window) WHERE b.created < w.created AND NOT b.label STARTS WITH 'pause-'
                       AND coalesce(b.steady, true) RETURN b.id ORDER BY b.created DESC LIMIT 1""", {"w": window})
        if not rows:
            return None
        baseline = rows[0][0]
    base = baseline
    if base == window:
        return None
    hb = {h["op"]: h for h in root_health(st, base)}
    hw = {h["op"]: h for h in root_health(st, window, baseline_p50={op: h["p50_ms"] for op, h in hb.items()})}
    for op, w in hw.items():
        b = hb.get(op)
        if not b:
            continue
        worse_fail = w["fail_rate"] > b["fail_rate"] + 0.05
        worse_p95 = w["p95_ms"] > 1.5 * b["p95_ms"]
        if not (worse_fail or worse_p95):
            continue
        owners_w = critical_owners(st, window, op, include_hangs=True)
        owners_b = {label(o): o["per_request_ms"] for o in critical_owners(st, base, op)}
        moved = sorted(owners_w, key=lambda o: -(o["per_request_ms"] - owners_b.get(label(o), 0.0)))
        top = moved[0] if moved else None
        _, worst = st.ro("MATCH (t:Trace {window:$w, root:$op}) RETURN t.id, t.duration_ms, t.status ORDER BY t.duration_ms DESC LIMIT 1", {"w": window, "op": op})
        _, wlabel = st.ro("MATCH (x:Window {id:$w}) MATCH (y:Window {id:$b}) RETURN x.label, x.start_us, x.end_us, y.label", {"w": window, "b": base})
        meta = wlabel[0] if wlabel else ("", None, None, "")
        return {"window": window, "label": meta[0], "started_us": meta[1], "ended_us": meta[2], "baseline": base, "baseline_label": meta[3], "endpoint": op, "service": w["service"],
                "fail_rate": (b["fail_rate"], w["fail_rate"]), "p50_ms": (b["p50_ms"], w["p50_ms"]), "p95_ms": (b["p95_ms"], w["p95_ms"]),
                "traces": w["traces"], "kind": "failures" if worse_fail else "latency",
                "owner": {**top, "baseline_per_request_ms": owners_b.get(label(top), 0.0)} if top else None,
                "hop": f"{top['service']} -> {top['peer']}" if top and top.get("peer") else (top["service"] if top else ""),
                "worst_trace": {"id": worst[0][0], "duration_ms": round(worst[0][1], 1), "status": worst[0][2]} if worst else None}
    return None


def incidents(st: Store) -> list[dict]:
    """Every window that reads as an incident against the steady window before it, newest first, with a suggested action."""
    from . import ingest
    out = []
    for w in reversed(ingest.windows(st)):
        inc = incident(st, w["id"])
        if inc:
            hop = inc.get("hop") or ""
            if " -> " in hop:
                inc["suggested"] = {"type": "guardrail", "payload": {"type": "guardrail", "hop": hop}, "title": f"Add a timeout on {hop}",
                                    "why": "the request waited on this hop; a timeout turns a hang into a fast failure"}
            else:
                inc["suggested"] = {"type": "issue", "payload": {"type": "issue", "window": inc["window"]}, "title": "File the incident with its evidence",
                                    "why": "no single hop to guard; the write-up carries the owner, numbers and worst trace"}
            out.append(inc)
    return out


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def contention(st: Store, window: str, min_spans: int = 20) -> list[dict]:
    """Lock or pool contention: an operation whose spans get slower the more of them are in flight at once.
    For every span count how many same-operation spans had started and not finished when it began, bucket by that
    count, and compare the least-crowded bucket with the most-crowded one. Evidence a judge can check in the waterfall."""
    _, rows = st.ro("MATCH (s:Span {window:$w}) RETURN s.service, s.op, s.start_us, s.duration_us ORDER BY s.start_us", {"w": window})
    by_op: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for svc, op, start, dur in rows:
        by_op.setdefault((svc, op), []).append((start, dur))
    out = []
    for (svc, op), spans in by_op.items():
        if len(spans) < min_spans:
            continue
        buckets: dict[int, list[float]] = {}
        for i, (start, dur) in enumerate(spans):
            inflight = sum(1 for s2, d2 in spans[max(0, i - 50):i] if s2 + d2 > start)
            buckets.setdefault(inflight, []).append(dur / 1000)
        xs = [k for k, v in buckets.items() for _ in v]; ys = [d for k, v in buckets.items() for d in v]
        if len(set(xs)) < 2:
            continue
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)); vx = sum((x - mx) ** 2 for x in xs); vy = sum((y - my) ** 2 for y in ys)
        corr = cov / ((vx * vy) ** 0.5) if vx and vy else 0.0
        slope = cov / vx if vx else 0.0            # ms added per extra span in flight
        alone = my - slope * mx                    # estimated duration with nothing else in flight
        hi = max(xs)
        if alone > 0 and slope >= 0.5 * alone and corr >= 0.4:
            out.append({"service": svc, "op": op, "alone_ms": round(alone, 1), "crowded_ms": round(alone + slope * hi, 1),
                        "ratio": round((alone + slope * hi) / alone, 2), "inflight_low": 0, "inflight_high": hi,
                        "per_inflight_ms": round(slope, 1), "correlation": round(corr, 2), "spans": len(spans)})
    return sorted(out, key=lambda r: -r["ratio"])


def serialized_fanout(st: Store, window: str, min_calls: float = 3.0) -> list[dict]:
    """A hop that fans out into many calls per request but runs them only a few at a time: the request pays for
    rounds of serialized calls. Measured as max concurrency of the hop's client spans within a trace."""
    _, rows = st.ro("""MATCH (c:Span {window:$w, kind:'client'}) WHERE c.peer <> ''
                       RETURN c.trace, c.service, c.peer, c.start_us, c.duration_us ORDER BY c.trace, c.start_us""", {"w": window})
    per_trace: dict[tuple[str, str, str], list[tuple[int, int]]] = {}
    for trace, svc, peer, start, dur in rows:
        per_trace.setdefault((trace, svc, peer), []).append((start, dur))
    hops: dict[tuple[str, str], list[tuple[int, int, float]]] = {}
    for (trace, svc, peer), spans in per_trace.items():
        if len(spans) < min_calls:
            continue
        events = sorted([(s, 1) for s, _ in spans] + [(s + d, -1) for s, d in spans])
        cur = peak = 0
        for _, delta in events:
            cur += delta; peak = max(peak, cur)
        hops.setdefault((svc, peer), []).append((len(spans), peak, _median([d / 1000 for _, d in spans])))
    out = []
    for (svc, peer), xs in hops.items():
        calls = _median([c for c, _, _ in xs]); peak = _median([p for _, p, _ in xs]); per_call = _median([m for _, _, m in xs])
        if calls >= min_calls and peak <= calls / 2:
            out.append({"hop": f"{svc} -> {peer}", "calls_per_request": round(calls, 1), "max_concurrency": int(peak),
                        "rounds": int(-(-calls // max(peak, 1))), "per_call_ms": round(per_call, 1), "traces": len(xs)})
    return sorted(out, key=lambda r: -r["rounds"])


FINDING_CYPHER = {
    "critical_owner": """MATCH (t:Trace {window:$w}) WITH t.root AS root, percentileDisc(t.duration_ms, 0.5) AS med, collect(t) AS ts
UNWIND ts AS t WITH t WHERE t.duration_ms <= 10 * med
WITH collect(t) AS ts, sum(t.duration_ms) AS total UNWIND ts AS t
MATCH (s:Span {critical:true})-[:OF]->(t)
RETURN s.service, s.op, s.peer, round(100.0*sum(s.self_us)/1000/total, 1) AS share_pct ORDER BY share_pct DESC""",
    "fan_out": "MATCH (a:Service)-[c:CALLS {window:$w}]->(b:Service) WHERE c.per_trace >= 3 RETURN a.name, b.name, c.per_trace, c.errors, c.p95_ms ORDER BY c.per_trace DESC",
    "hang_not_fail": """MATCH (t:Trace {window:$w}) WITH t.root AS root, percentileDisc(t.duration_ms, 0.5) AS med, collect(t) AS ts
UNWIND ts AS t WITH root, med, t WHERE t.duration_ms > 10 * med
MATCH (s:Span {critical:true})-[:OF]->(t) WITH root, med, t, s ORDER BY s.self_us DESC
RETURN root, med, t.id, t.duration_ms, t.status, collect({service:s.service, op:s.op, peer:s.peer})[0] AS worst ORDER BY t.duration_ms DESC""",
    "silent_retry": """MATCH (c1:Span {window:$w, error:true})-[:CHILD_OF]->(p:Span)<-[:CHILD_OF]-(c2:Span)
WHERE c1.op = c2.op AND c1.service = c2.service AND c2.start_us > c1.start_us
RETURN c1.service, c1.op, count(DISTINCT c1) AS retried_errors, sum(c1.duration_us)/1000.0 AS wasted_ms""",
    "exposure": """MATCH (edge:Service {name:'frontend'}) MATCH (store:Service)<-[c:CALLS {window:$w}]-() WHERE c.proto STARTS WITH 'db:'
WITH DISTINCT edge, store MATCH p = (edge)-[:CALLS*1..6 {window:$w}]->(store)
RETURN store.name, [n IN nodes(p) | n.name] AS path, [r IN relationships(p) | r.proto] AS protocols""",
    "single_point_of_failure": """CALL algo.betweenness({nodeLabels:['Service'], relationshipTypes:['CALLS']}) YIELD node, score
MATCH path = (r:Service)-[:CALLS* {window:$w}]->(node) WHERE NOT ()-[:CALLS {window:$w}]->(r)
RETURN node.name, score, count(DISTINCT path) AS inbound_paths ORDER BY score DESC""",
}


def status(st: Store, window: str) -> dict:
    """Health of a window against the steady window before it, with the same thresholds as incident()."""
    rows = st.q("""MATCH (w:Window {id:$w}) OPTIONAL MATCH (b:Window) WHERE b.created < w.created AND NOT b.label STARTS WITH 'pause-'
                   AND coalesce(b.steady, true) WITH w, b ORDER BY b.created DESC LIMIT 1 RETURN w.label, b.id, b.label""", {"w": window})
    label, base, base_label = rows[0] if rows else ("", None, None)
    health = {h["op"]: h for h in root_health(st, window)}
    if not base or not health:
        return {"window": window, "label": label, "status": "baseline", "baseline": None, "reasons": ["first window: nothing to compare against"], "deltas": None}
    hb = {h["op"]: h for h in root_health(st, base)}
    hw = {h["op"]: h for h in root_health(st, window, baseline_p50={op: h["p50_ms"] for op, h in hb.items()})}
    op = next(iter(hw))
    a, b = hw[op], hb.get(op, hw[op])
    deltas = {"p50_pct": round(100.0 * (a["p50_ms"] - b["p50_ms"]) / max(b["p50_ms"], 1), 1), "p95_pct": round(100.0 * (a["p95_ms"] - b["p95_ms"]) / max(b["p95_ms"], 1), 1),
              "fail_rate_pts": round(100.0 * (a["fail_rate"] - b["fail_rate"]), 1)}
    reasons = []
    if a["fail_rate"] > b["fail_rate"] + 0.05:
        reasons.append(f"failure rate {b['fail_rate']:.0%} -> {a['fail_rate']:.0%}")
    if a["p95_ms"] > 1.5 * b["p95_ms"]:
        reasons.append(f"p95 {b['p95_ms']:.0f} -> {a['p95_ms']:.0f} ms")
    st_ = "failing" if a["fail_rate"] >= 0.5 else "degraded" if reasons else "ok"
    if not reasons:
        reasons.append(f"within its baseline {base_label}" + (f"; p50 {b['p50_ms']:.0f} -> {a['p50_ms']:.0f} ms" if a["p50_ms"] < b["p50_ms"] else ""))
    from . import config
    slo = None
    if config.SLO_P95_MS or config.SLO_FAIL_RATE:
        slo = {"p95_ok": config.SLO_P95_MS is None or a["p95_ms"] <= config.SLO_P95_MS, "fail_ok": config.SLO_FAIL_RATE is None or a["fail_rate"] <= config.SLO_FAIL_RATE}
    return {"window": window, "label": label, "status": st_, "baseline": base, "baseline_label": base_label, "endpoint": op, "reasons": reasons, "deltas": deltas, "slo": slo}


def traces(st: Store, window: str, order: str = "duration", errors: bool = False, limit: int = 20) -> list[dict]:
    """Root traces of a window: slowest first (default) or in time order, optionally only failed/erroring ones."""
    order_by = "t.duration_ms DESC" if order == "duration" else "t.start_us"
    _, rows = st.ro(f"""MATCH (t:Trace {{window:$w}}) WHERE NOT $errors OR t.errors > 0 OR NOT t.status STARTS WITH '2'
                        RETURN t.id, t.root, t.duration_ms, t.status, t.errors, t.start_us ORDER BY {order_by} LIMIT $n""",
                    {"w": window, "errors": errors, "n": int(limit)})
    return [{"id": r[0], "root": r[1], "duration_ms": round(r[2], 1), "status": r[3], "errors": r[4], "start_us": r[5]} for r in rows]


def findings(st: Store, window: str) -> list[dict]:
    out: list[dict] = []
    for o in critical_owners(st, window)[:3]:
        if o["share_pct"] >= 15:
            c = o.get("code")
            out.append({"kind": "critical_owner", "severity": "high" if o["share_pct"] >= 30 else "medium", "service": o["service"],
                        "detail": f"{label(o)} owns {o['share_pct']}% of steady-state end-to-end latency on the critical path "
                                  f"({o['per_request_ms']:.0f} ms per request, {o['spans']} critical spans)"
                                  + (f"; code: {c.get('location') or c.get('file')}" + (f" {c['function']}" if c.get('function') else "") if c else ""),
                        "code": c})
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
    for e in exposure(st, window):
        out.append({"kind": "exposure", "severity": "medium", "service": e["store"],
                    "detail": f"{e['store_kind']} store {e['store']} is {e['hops']} hop(s) from the edge ({e['path']}); "
                              f"{e['calls_per_request_into_store']} calls per request reach it"
                              + (f"; {len(e['notes'])} hop(s) over {'/'.join(sorted({p for p in e['protocols'] if p in ('http', 'grpc')}))} with no TLS attribute recorded" if e["notes"] else "")})
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


def with_cypher(items: list[dict], window: str) -> list[dict]:
    for f in items:
        q = FINDING_CYPHER.get(f["kind"])
        if q:
            f["cypher"] = q; f["params"] = {"w": window}
    return items


def record_findings(st: Store, window: str, items: list[dict]) -> int:
    st.q("MATCH (f:Finding {window:$w}) DETACH DELETE f", {"w": window})
    for i, f in enumerate(items):
        st.q("""MATCH (s:Service {name:$svc}) CREATE (f:Finding {id:$id, window:$w, kind:$kind, severity:$sev, service:$svc, detail:$detail})
                CREATE (f)-[:ABOUT]->(s)""", {"id": f"{window}-f{i}", "w": window, **{"kind": f["kind"], "sev": f["severity"], "svc": f["service"], "detail": f["detail"]}})
    return len(items)
