"""Closed-loop remediation: the agent proves its own fix before anything is proposed.

1. Plan a compatible set of version bumps (planner.py).
2. Build the candidate dependency set into a private scratch graph (real sources, real advisories).
3. Re-run triage there and compare to the baseline through explicit gates.
4. Persist the outcome (and its snapshot) so an approval can be bound to exactly this evidence.

Status semantics: UNVERIFIED means the check could not be trusted (analysis coverage dropped), never "fixed".
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
import uuid

from . import analysis, config, guard, ingest, planner, targets
from .graph import Store

EXPOSED = {"REACHABLE", "POSSIBLY_REACHABLE"}
MIN_COVERAGE_RATIO = 0.5  # a candidate release analysed with <50% of the baseline's functions is not comparable

# Public (read-only) mode may still run the closed loop *ephemerally*: the scratch graph is private and deleted, and
# nothing is written to the shared graph. One verification at a time, with a cooldown, so visitors cannot pile up
# scratch graphs on the shared FalkorDB instance.
PUBLIC_COOLDOWN_SECONDS = 30
_public_lock = threading.Lock()
_public_last_finished = 0.0


class BusyError(RuntimeError):
    """Another visitor's verification is running, or the cooldown has not elapsed."""


def coverage(st: Store) -> dict[str, int]:
    """Functions analysed per parsed dependency package."""
    out: dict[str, int] = {}
    for dist, n in st.q("MATCH (f:Function {layer:'dep'}) RETURN f.dist, count(f)"):
        out[dist.split("==")[0]] = n
    return out


def summarize(findings: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
    return {"counts": counts, "exposed": sorted(f["vuln"] for f in findings if f["verdict"] in EXPOSED),
            "verdicts": {f["vuln"]: f["verdict"] for f in findings}}


def requirements_diff(pins_after: dict[str, str], target: targets.Target | None = None) -> str:
    target = target or targets.get(None)
    path = target.requirements
    old = path.read_text().splitlines(keepends=True)
    new = []
    for line in old:
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([\w.]+)", line.strip())
        if m and m.group(1) in pins_after:
            line = f"{m.group(1)}=={pins_after[m.group(1)]}\n"
        new.append(line)
    return "".join(difflib.unified_diff(old, new, f"a/{target.repo_path}", f"b/{target.repo_path}"))


def verify_plan(st: Store, plan: planner.Plan, progress=print, keep_scratch: bool = False,
                persist: bool = True) -> dict:
    """Verify `plan` in a private scratch graph. With `persist=False` the result is returned but NOT written to the
    shared graph (what the read-only public site uses); with `persist=True` a Remediation node is created."""
    if persist:
        guard.require_writable("Remediation verification")
    baseline_findings = analysis.latest_findings(st)
    base_run = baseline_findings[0]["run"]
    before = summarize(baseline_findings)
    before_cov = coverage(st)

    target = targets.of_store(st)
    scratch = Store(f"{target.graph}_scratch_{uuid.uuid4().hex[:8]}", ephemeral=True)
    try:
        progress("building candidate dependency set into a scratch graph")
        ingest.full_ingest(scratch, progress=lambda m: progress(f"  scratch: {m}"), pins=plan.pins_after, target=target,
                            parse=[r[0] for r in st.q("MATCH (p:PackageVersion {parsed:true}) RETURN p.name")])
        run = analysis.triage(scratch, label="candidate")
        after_findings = analysis.latest_findings(scratch, run)
        after = summarize(after_findings)
        after_cov = coverage(scratch)
        candidate_snapshot = analysis.run_snapshot(scratch, run)
    finally:
        if not keep_scratch:
            try:
                scratch.g.delete()
            except Exception:
                pass

    cov = {}
    for pkg, n_before in before_cov.items():
        n_after = after_cov.get(pkg, 0)
        cov[pkg] = {"before": n_before, "after": n_after, "ratio": round(n_after / n_before, 2) if n_before else 0.0}
    cov_ok = all(c["after"] > 0 and c["ratio"] >= MIN_COVERAGE_RATIO for c in cov.values())
    new_exposed = sorted(set(after["exposed"]) - set(before["exposed"]))
    cleared = sorted(set(before["exposed"]) - set(after["exposed"]))
    gates = [
        {"name": "analysis coverage retained", "passed": cov_ok,
         "detail": ", ".join(f"{p} {c['before']}->{c['after']} fns" for p, c in cov.items())},
        {"name": "dependency constraints consistent", "passed": plan.consistent,
         "detail": "; ".join(plan.conflicts) or "all pinned requirements satisfied"},
        {"name": "exposure decreases", "passed": len(after["exposed"]) < len(before["exposed"]),
         "detail": f"{len(before['exposed'])} -> {len(after['exposed'])} exposed advisories"},
        {"name": "no new exposure", "passed": not new_exposed,
         "detail": ", ".join(new_exposed) or "no advisory became exposed"},
    ]
    if not gates[0]["passed"]:
        status = "UNVERIFIED"
    elif all(g["passed"] for g in gates):
        status = "VERIFIED"
    else:
        status = "REJECTED"

    result = {
        "id": f"rem-{uuid.uuid4().hex[:8]}", "status": status, "gates": gates, "before": before["counts"],
        "after": after["counts"], "cleared": cleared, "new_exposed": new_exposed,
        "remaining_exposed": after["exposed"], "coverage": cov, "plan": plan.as_dict(),
        "baseline_run": base_run, "baseline_snapshot": analysis.run_snapshot(st, base_run),
        "candidate_snapshot": candidate_snapshot, "requirements_diff": requirements_diff(plan.pins_after, target),
        "ts": int(time.time()), "persisted": persist,
    }
    if not persist:
        return result
    st.q("MATCH (r:Run {id:$run}) CREATE (m:Remediation {id:$id, status:$status, result:$result, ts:$ts, "
         "baseline_snapshot:$snap})-[:BASED_ON]->(r)",
         {"run": base_run, "id": result["id"], "status": status, "result": json.dumps(result), "ts": result["ts"],
          "snap": result["baseline_snapshot"]})
    return result


def run_closed_loop(st: Store, progress=print, persist: bool = True) -> dict:
    progress("planning version-aware upgrades from the graph")
    plan = planner.plan_remediation(st)
    return verify_plan(st, plan, progress, persist=persist)


def run_closed_loop_ephemeral(st: Store, progress=print) -> dict:
    """The closed loop for the read-only public site: live, in a private scratch graph that is deleted afterwards,
    nothing written to the shared graph, one at a time, with a cooldown. Raises BusyError instead of queueing."""
    global _public_last_finished
    if not _public_lock.acquire(blocking=False):
        raise BusyError("another visitor's verification is running right now; try again in a moment")
    try:
        wait = PUBLIC_COOLDOWN_SECONDS - (time.time() - _public_last_finished)
        if wait > 0:
            raise BusyError(f"a verification just finished; try again in {int(wait) + 1}s")
        try:
            return run_closed_loop(st, progress, persist=False)
        finally:
            _public_last_finished = time.time()
    finally:
        _public_lock.release()


def latest_remediation(st: Store) -> dict | None:
    rows = st.q("MATCH (m:Remediation) RETURN m.result ORDER BY m.ts DESC LIMIT 1")
    return json.loads(rows[0][0]) if rows else None


def render_report(res: dict) -> str:
    lines = [f"# Graph-checked remediation `{res['id']}`: **{res['status']}**", "",
             f"Baseline run `{res['baseline_run']}` (snapshot `{res['baseline_snapshot']}`), "
             f"candidate snapshot `{res['candidate_snapshot']}`.", "", "## Gates"]
    lines += [f"- {'PASS' if g['passed'] else 'FAIL'}: {g['name']}: {g['detail']}" for g in res["gates"]]
    lines += ["", "## Before / after (verdict counts)", f"- before: {res['before']}", f"- after: {res['after']}",
              f"- cleared: {', '.join(res['cleared']) or 'none'}", "", "## Changes"]
    lines += [f"- {c['package']} {c['from']} -> {c['to']}: {c['reason']}" for c in res["plan"]["changes"]]
    if res["plan"]["rejected"]:
        lines += ["", "## Rejected on its own"] + [f"- {r['reason']} (resolution: {r['resolution']})" for r in res["plan"]["rejected"]]
    lines += ["", "```diff", res["requirements_diff"].rstrip(), "```", "",
              "_Static analysis only. 'Graph-checked' means the rebuilt graph shows these advisories are no longer "
              "exposed with the same analysis coverage; it is not a guarantee of security._"]
    return "\n".join(lines) + "\n"
