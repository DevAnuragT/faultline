"""Reachability triage. The verdict is computed by graph queries, never by the LLM.

Verdicts
  REACHABLE           a public endpoint reaches the vulnerable function through exact (resolved) calls only
  POSSIBLY_REACHABLE  a path exists but needs low-confidence edges (name-guess, registry dispatch, value references)
  NEEDS_REVIEW        static analysis cannot decide: no symbol known, package not analyzed, or the symbol is
                      only referenced as a value (e.g. a runtime plugin / filter registry)
  NO_STATIC_PATH      no static path found. Dynamic behaviour is not modelled, so this is NOT a proof of safety.
"""
from __future__ import annotations

import time
import uuid

from packaging.version import Version

from .graph import Store

EXACT_PATH = """
MATCH (v:Vuln {id:$id})-[:VULNERABLE_SYMBOL]->(f:Function), (e:Endpoint {public:true})-[:HANDLED_BY]->(h:Function)
WITH v, f, e, h, shortestPath((h)-[:CALLS*1..14]->(f)) AS p
WHERE p IS NOT NULL
RETURN e.id AS endpoint, f.qualname AS symbol, [n IN nodes(p) | n.qualname] AS path,
       [n IN nodes(p) | n.layer] AS layers, [r IN relationships(p) | type(r)] AS kinds
ORDER BY length(p) LIMIT 1
"""

ANY_PATH = """
MATCH (v:Vuln {id:$id})-[:VULNERABLE_SYMBOL]->(f:Function), (e:Endpoint {public:true})-[:HANDLED_BY]->(h:Function)
WITH v, f, e, h, shortestPath((h)-[:CALLS|MAYBE_CALLS|REFERENCES*1..14]->(f)) AS p
WHERE p IS NOT NULL
RETURN e.id AS endpoint, f.qualname AS symbol, [n IN nodes(p) | n.qualname] AS path,
       [n IN nodes(p) | n.layer] AS layers, [r IN relationships(p) | type(r)] AS kinds
ORDER BY length(p) LIMIT 1
"""

VALUE_REFS = """
MATCH (v:Vuln {id:$id})-[:VULNERABLE_SYMBOL]->(f:Function)<-[:REFERENCES]-(r:Function)
RETURN f.qualname, collect(DISTINCT r.qualname)[0..3]
"""

VULN_INFO = """
MATCH (v:Vuln)-[:AFFECTS]->(p:PackageVersion)
OPTIONAL MATCH (v)-[s:VULNERABLE_SYMBOL]->(f:Function)
RETURN v.id, v.cve, v.package, p.version, p.parsed, v.severity, v.fixed_version, v.summary,
       collect(f.qualname) AS symbols
ORDER BY v.package, v.id
"""


def triage(st: Store, label: str = "baseline") -> str:
    """Compute a verdict for every advisory, persist Finding nodes with witness paths. Returns the run id."""
    run_id = f"run-{int(time.time())}-{uuid.uuid4().hex[:4]}"
    st.q("CREATE (:Run {id:$id, label:$label, ts:$ts})", {"id": run_id, "label": label, "ts": time.time_ns()})
    for vid, cve, pkg, ver, parsed, sev, fixed, summary, symbols in st.q(VULN_INFO):
        verdict, reason, path, layers, kinds, endpoint = _decide(st, vid, bool(parsed), symbols)
        fid = f"{run_id}:{vid}"
        st.q("MATCH (r:Run {id:$run}), (v:Vuln {id:$vid}) CREATE (r)-[:HAS_FINDING]->(f:Finding {id:$fid, verdict:$verdict, "
             "reason:$reason, path_len:$plen, run:$run})-[:FOR]->(v)",
             {"run": run_id, "vid": vid, "fid": fid, "verdict": verdict, "reason": reason,
              "plen": max(len(path) - 1, 0)})
        if endpoint:
            st.q("MATCH (f:Finding {id:$fid}), (e:Endpoint {id:$e}) CREATE (f)-[:VIA]->(e)", {"fid": fid, "e": endpoint})
        for i, (q, layer) in enumerate(zip(path, layers)):
            kind = kinds[i - 1] if i > 0 else "ENTRY"
            st.q("MATCH (f:Finding {id:$fid}), (n:Function {qualname:$q}) CREATE (f)-[:EVIDENCE {step:$i, via:$k}]->(n)",
                 {"fid": fid, "q": q, "i": i, "k": kind})
    return run_id


def _decide(st: Store, vid: str, parsed: bool, symbols: list[str]):
    none = ([], [], [], None)
    if not parsed:
        return ("NEEDS_REVIEW", "Package source is not analyzed, so no call path can be computed.", *none)
    if not symbols:
        return ("NEEDS_REVIEW", "No vulnerable function could be identified from the advisory/fix commit.", *none)
    rows = st.q(EXACT_PATH, {"id": vid})
    if rows:
        endpoint, symbol, path, layers, kinds = rows[0]
        return ("REACHABLE", f"Public endpoint {endpoint} reaches {symbol} through exact call edges.",
                path, layers, kinds, endpoint)
    rows = st.q(ANY_PATH, {"id": vid})
    if rows:
        endpoint, symbol, path, layers, kinds = rows[0]
        weak = sorted({k for k in kinds if k != "CALLS"})
        return ("POSSIBLY_REACHABLE",
                f"Path from {endpoint} to {symbol} exists but relies on low-confidence edges ({', '.join(weak)}).",
                path, layers, kinds, endpoint)
    refs = st.q(VALUE_REFS, {"id": vid})
    if refs:
        return ("NEEDS_REVIEW",
                f"No direct path, but {refs[0][0].split('.')[-1]} is referenced as a value (plugin/filter registry "
                "dispatch is dynamic): confirm it is never selected at runtime.", *none)
    return ("NO_STATIC_PATH", "No static path from any public endpoint. Dynamic behaviour is not modelled; "
            "not a proof of safety.", *none)


def latest_findings(st: Store, run_id: str | None = None) -> list[dict]:
    run_clause = "{id:$run}" if run_id else ""
    q = f"""
    MATCH (r:Run {run_clause})-[:HAS_FINDING]->(f:Finding)-[:FOR]->(v:Vuln)-[:AFFECTS]->(p:PackageVersion)
    WITH r, f, v, p ORDER BY r.ts DESC
    RETURN f.id, r.id, r.label, f.verdict, f.reason, v.id, v.cve, v.package, p.version, v.severity,
           v.fixed_version, v.summary, v.symbol_source, v.symbol_confidence, v.symbol_rationale, v.patch_url
    """
    rows = st.q(q, {"run": run_id} if run_id else {})
    if not rows:
        return []
    top = rows[0][1] if not run_id else run_id
    out = []
    for r in rows:
        if r[1] != top:
            continue
        out.append(dict(finding=r[0], run=r[1], label=r[2], verdict=r[3], reason=r[4], vuln=r[5], cve=r[6] or None,
                        package=r[7], version=r[8], severity=r[9], fixed_version=r[10] or None, summary=r[11],
                        symbol_source=r[12], symbol_confidence=r[13], symbol_rationale=r[14], patch_url=r[15]))
    order = {"REACHABLE": 0, "POSSIBLY_REACHABLE": 1, "NEEDS_REVIEW": 2, "NO_STATIC_PATH": 3}
    return sorted(out, key=lambda d: (order[d["verdict"]], d["package"], d["vuln"]))


def witness_path(st: Store, finding_id: str) -> list[dict]:
    rows = st.q("MATCH (f:Finding {id:$id})-[e:EVIDENCE]->(n:Function) RETURN e.step, n.qualname, n.layer, e.via "
                "ORDER BY e.step", {"id": finding_id})
    return [{"step": r[0], "function": r[1], "layer": r[2], "via": r[3]} for r in rows]


def guard_points(st: Store, run_id: str) -> list[dict]:
    """App functions that sit on the most witness paths: patching/guarding them removes the most exposure."""
    rows = st.q("""
    MATCH (r:Run {id:$run})-[:HAS_FINDING]->(f:Finding)-[ev:EVIDENCE]->(n:Function {layer:'app'})
    WHERE f.verdict IN ['REACHABLE','POSSIBLY_REACHABLE'] AND NOT n.qualname ENDS WITH '.<module>'
    MATCH (f)-[:FOR]->(v:Vuln)
    RETURN n.qualname, count(DISTINCT f) AS findings, collect(DISTINCT v.package) AS packages, max(ev.step) AS depth
    ORDER BY findings DESC, depth DESC, n.qualname
    """, {"run": run_id})
    return [{"function": r[0], "findings": r[1], "packages": r[2]} for r in rows]


def upgrade_plan(st: Store, run_id: str) -> list[dict]:
    """Which single package bump clears the most exposed findings (aggregated over the graph)."""
    rows = st.q("""
    MATCH (r:Run {id:$run})-[:HAS_FINDING]->(f:Finding)-[:FOR]->(v:Vuln)-[:AFFECTS]->(p:PackageVersion)
    WHERE f.verdict IN ['REACHABLE','POSSIBLY_REACHABLE']
    RETURN p.name, p.version, collect(DISTINCT v.id), collect(DISTINCT v.fixed_version)
    """, {"run": run_id})
    plan = []
    for name, version, vids, fixed in rows:
        versions = [Version(x) for x in fixed if x]
        target = max(versions) if versions else None
        plan.append({"package": name, "installed": version, "upgrade_to": str(target) if target else None,
                     "clears": len(vids), "advisories": vids})
    return sorted(plan, key=lambda d: -d["clears"])


def owner_of(st: Store, function: str) -> str | None:
    rows = st.q("MATCH (t:Team)-[:OWNS]->(:Module)-[:DEFINES]->(f:Function {qualname:$q}) RETURN t.name", {"q": function})
    return rows[0][0] if rows else None


def supply_chain_chokepoints(st: Store) -> list[dict]:
    """Betweenness over the dependency graph: packages many dependency paths run through."""
    rows = st.q("""
    CALL algo.betweenness({nodeLabels:['PackageVersion','Repo'], relationshipTypes:['DEPENDS_ON']})
    YIELD node, score
    RETURN labels(node)[0], coalesce(node.key, node.name), score ORDER BY score DESC
    """)
    return [{"kind": r[0], "node": r[1], "betweenness": r[2]} for r in rows if r[2] > 0]
