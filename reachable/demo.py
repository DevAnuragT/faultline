"""`make demo`: rebuild everything from a clean ingest in a throwaway graph and print every number the pitch uses.

Nothing here is hard-coded: if a number appears in the README or the video, it must appear in this output.
"""
from __future__ import annotations

import time

from . import actions, analysis, frozen, ingest, planner, verify
from .graph import Store

DEMO_GRAPH = "reachable_demo"


def run(progress=print) -> dict:
    st = Store(DEMO_GRAPH)
    out: dict = {}
    t0 = time.time()
    progress("1/5 clean ingest (call graph from real package sources, supply chain, OSV advisories)")
    out["ingest"] = ingest.full_ingest(st, progress=lambda *_: None)
    run_id = analysis.triage(st)
    findings = analysis.latest_findings(st, run_id)
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
    out["advisories"] = len(findings)
    out["verdicts"] = counts
    out["exposed"] = counts.get("REACHABLE", 0) + counts.get("POSSIBLY_REACHABLE", 0)

    progress("2/5 witness path for the flagship advisory")
    f = next(x for x in findings if x["vuln"] == "GHSA-q2q7-5pp4-w6pg")
    path = analysis.witness_path(st, f["finding"])
    out["flagship"] = {"vuln": f["vuln"], "verdict": f["verdict"], "hops": len(path) - 1,
                      "path": [p["function"] for p in path], "exact_only": all(p["via"] in ("ENTRY", "CALLS") for p in path)}

    progress("3/5 version-aware plan")
    plan = planner.plan_remediation(st, run_id)
    out["plan"] = {"changes": [f"{c['package']} {c['from']}->{c['to']}" for c in plan.changes],
                   "rejected": [r["reason"] for r in plan.rejected], "consistent": plan.consistent}

    progress("4/5 closed loop: scratch graph with candidate versions, re-triage, gates")
    rem = verify.verify_plan(st, plan, progress=lambda *_: None)
    out["remediation"] = {"status": rem["status"], "before": rem["before"], "after": rem["after"],
                          "cleared": len(rem["cleared"]), "new_exposed": len(rem["new_exposed"]),
                          "gates": {g["name"]: g["passed"] for g in rem["gates"]}}
    out["approval"] = actions.propose_fix_pr(st)

    progress("5/5 what-if: remove the outbound HTTP call from /preview")
    ingest.reload_app_layer(st, "no_preview_fetch")
    alt = analysis.triage(st, label="no_preview_fetch")
    after = {x["vuln"]: x["verdict"] for x in analysis.latest_findings(st, alt)}
    before = {x["vuln"]: x["verdict"] for x in findings}
    out["whatif"] = {"flips": sum(1 for v in before if before[v] != after[v])}
    out["seconds"] = round(time.time() - t0, 1)
    st.g.delete()
    return out


def main() -> None:
    r = run()
    print("\n=== numbers used in the pitch (reproduced from a clean ingest) ===")
    print(f"advisories: {r['advisories']}   verdicts: {r['verdicts']}   exposed: {r['exposed']}")
    fl = r["flagship"]
    print(f"flagship {fl['vuln']}: {fl['verdict']}, {fl['hops']} hops, exact edges only: {fl['exact_only']}")
    print("  " + " -> ".join(p.split('.')[-1] for p in fl["path"]))
    print(f"plan: {r['plan']['changes']}  consistent: {r['plan']['consistent']}")
    for reason in r["plan"]["rejected"]:
        print(f"  rejected: {reason}")
    rem = r["remediation"]
    print(f"closed loop: {rem['status']}  before {rem['before']}  after {rem['after']}  cleared {rem['cleared']}  new {rem['new_exposed']}")
    print(f"  gates: {rem['gates']}")
    print(f"draft PR proposal: {r['approval'].get('status')} ({r['approval'].get('action')})")
    print(f"what-if flips: {r['whatif']['flips']}")
    print(f"done in {r['seconds']}s")


if __name__ == "__main__":
    main()
