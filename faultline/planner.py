"""Decide what to do next, from the graph.

Experiment ranking follows Netflix's Monocle (Basiri et al., ICSE-SEIP 2019): rank by criticality, discount by safety
and recency, skip targets whose last experiment is still awaiting review. Here every term is a graph query, and the
"memory" is the Experiment / Fix ledger in the same graph.
"""
from __future__ import annotations

import math
import time

from . import analysis
from .graph import Store

PAUSABLE = ("frontend", "customer", "driver", "route")
RECENT_S = 15 * 60


def rank_experiments(st: Store, window: str) -> list[dict]:
    traffic = {s["service"]: s for s in analysis.single_points_of_failure(st, window)}
    max_b = max([s["betweenness"] for s in traffic.values()] + [1.0])
    total_calls = sum(s["inbound_calls"] for s in traffic.values()) or 1.0
    _, past = st.ro("MATCH (e:Experiment)-[:TARGETS]->(s:Service) RETURN s.name, e.verdict, e.lesson, e.ts, e.reviewed ORDER BY e.ts DESC")
    history: dict[str, dict] = {}
    for name, verdict, lesson, ts, reviewed in past:
        history.setdefault(name, {"verdict": verdict, "lesson": lesson, "ts": ts, "reviewed": bool(reviewed), "runs": 0})["runs"] += 1
    out = []
    for name in PAUSABLE:
        br = analysis.blast_radius(st, window, name)
        hit = sum(r["traces"] for r in br["root_operations"])
        _, tot = st.ro("MATCH (t:Trace {window:$w}) RETURN count(t)", {"w": window})
        blast_share = hit / (tot[0][0] or 1)
        c = traffic.get(name, {"betweenness": 0.0, "inbound_calls": 0})
        criticality = 0.4 * (c["betweenness"] / max_b) + 0.3 * (c["inbound_calls"] / total_calls) + 0.3 * blast_share
        h = history.get(name)
        safety = 1.0
        reasons = [f"blast radius {blast_share:.0%} of requests", f"betweenness {c['betweenness']:.0f}", f"{c['inbound_calls']:.0f} inbound calls"]
        skip = None
        if h:
            age = time.time() - (h["ts"] or 0)
            if h["verdict"] == "REFUTED" and not h["reviewed"]:
                skip = "last experiment was REFUTED and is awaiting review"
            elif age < RECENT_S:
                skip = f"ran {math.ceil(age / 60)} min ago"
            else:
                safety = 0.5  # known behaviour: lower novelty, Monocle's 'already tested' discount
                reasons.append(f"tested before: {h['verdict']}, {h['lesson'][:60]}")
        if name in st_edge_services():
            safety *= 0.5
            reasons.append("edge service: pausing it is an outage, not an experiment")
        priority = 0.0 if skip else criticality * safety
        out.append({"target": name, "priority": round(priority, 3), "criticality": round(criticality, 3), "blast_share": round(blast_share, 2),
                    "skip": skip, "reasons": reasons, "history": h})
    return sorted(out, key=lambda r: -r["priority"])


def st_edge_services() -> set[str]:
    from . import config
    return set(config.EDGE_SERVICES)


def next_fix(st: Store, window: str) -> dict | None:
    """Map the top critical-path owner to the fix HotROD exposes for it; the agent proposes it, a human approves."""
    from .experiments import FIX_FLAGS, OWNER_OF
    _, applied = st.ro("MATCH (f:Fix) RETURN f.flags, f.verdict, f.gates ORDER BY f.ts")
    used = {row[0].split()[0] for row in applied if row[1] == "VERIFIED"}
    previous = {row[0].split()[0]: {"verdict": row[1], "failed_gates": [g for g in row[2] if g.startswith("FAIL")]} for row in applied}
    owners = analysis.critical_owners(st, window)
    top = owners[0] if owners else None
    for o in owners:
        svc = OWNER_OF.get(o["service"], o["service"])
        for flag, (target, why) in (("-M", FIX_FLAGS["-M"]), ("-D 100ms", FIX_FLAGS["-D"]), ("-W 100", FIX_FLAGS["-W"])):
            base = flag.split()[0]
            if target != svc or base in used:
                continue
            if base == "-W" and o["service"] not in ("route", "frontend"):
                continue
            if base in ("-M", "-D") and o["service"] != "mysql":
                continue
            return {"flags": flag.split(), "service": target, "targets_owner": analysis.label(o), "share_pct": o["share_pct"],
                    "per_request_ms": o["per_request_ms"], "why": why, "previous_attempt": previous.get(base),
                    "note": None if o is top else f"no known knob targets the top owner {analysis.label(top)} ({top['share_pct']}%); "
                                                   f"this is the next owner with a knob"}
    return None


def guardrails(st: Store, window: str) -> list[dict]:
    """Timeouts, retry budgets and fan-out caps derived from measured latency and from experiment evidence."""
    out = []
    _, hops = st.ro("""
        MATCH (a:Service)-[c:CALLS {window:$w}]->(b:Service)
        OPTIONAL MATCH (e:Experiment {kind:'pause'})-[:TARGETS]->(b)
        WITH a, b, c, collect(e.lesson) AS lessons
        RETURN a.name, b.name, c.p95_ms, c.per_trace, c.errors, c.count, lessons""", {"w": window})
    for a, b, p95, per_trace, errors, count, lessons in hops:
        hung = any(l and "did not fail fast" in l for l in lessons)
        timeout = max(50, int(math.ceil(p95 * 3 / 50.0) * 50))
        out.append({"hop": f"{a} -> {b}", "kind": "timeout", "value": f"{timeout} ms",
                    "evidence": f"p95 {p95:.0f} ms over {count} calls" + ("; an experiment showed this hop hangs for 30 s+ when the callee stops" if hung else ""),
                    "priority": "high" if hung else "medium"})
        if per_trace >= 5:
            out.append({"hop": f"{a} -> {b}", "kind": "fan-out cap / batching", "value": f"{per_trace:.0f} calls per request today",
                        "evidence": "one request becomes many calls: batch them or cache, and bound concurrency", "priority": "medium"})
        if errors and count and errors / count > 0.1:
            out.append({"hop": f"{a} -> {b}", "kind": "retry budget", "value": "retries <= 10% of calls, with jitter",
                        "evidence": f"{errors}/{count} calls error ({errors / count:.0%}); see retries() for how many are retried", "priority": "medium"})
    return sorted(out, key=lambda r: (r["priority"] != "high", r["hop"]))


def diff_windows(st: Store, a: str, b: str) -> dict:
    """What changed between two windows: endpoint health, critical-path owners, and the dependency edges themselves."""
    ha = {h["op"]: h for h in analysis.root_health(st, a)}
    hb = {h["op"]: h for h in analysis.root_health(st, b)}
    health = [{"op": op, "p50_ms": (ha[op]["p50_ms"], hb[op]["p50_ms"]), "p95_ms": (ha[op]["p95_ms"], hb[op]["p95_ms"]),
               "fail_rate": (ha[op]["fail_rate"], hb[op]["fail_rate"])} for op in ha if op in hb]
    oa = {analysis.label(o): o for o in analysis.critical_owners(st, a)}
    ob = {analysis.label(o): o for o in analysis.critical_owners(st, b)}
    owners = sorted([{"owner": k, "per_request_ms": (oa.get(k, {}).get("per_request_ms", 0), ob.get(k, {}).get("per_request_ms", 0))}
                     for k in set(oa) | set(ob)], key=lambda r: -(abs(r["per_request_ms"][0] - r["per_request_ms"][1])))
    ea = {(e["from"], e["to"]) for e in analysis.service_graph(st, a)}
    eb = {(e["from"], e["to"]) for e in analysis.service_graph(st, b)}
    return {"from": a, "to": b, "health": health, "owners": owners[:6],
            "edges_added": sorted(f"{x} -> {y}" for x, y in eb - ea), "edges_removed": sorted(f"{x} -> {y}" for x, y in ea - eb)}
