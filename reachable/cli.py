"""Command line:
  python -m reachable.cli ingest | triage | whatif <variant> | ask "<question>"
  python -m reachable.cli plan | remediate | propose-pr | approve <action> | freeze | record-agent | seed
"""
from __future__ import annotations

import json
import sys

from . import actions, analysis, frozen, ingest, planner, verify
from .agent import Agent
from .graph import Store


def _print_findings(st: Store) -> None:
    for f in analysis.latest_findings(st):
        print(f"{f['verdict']:19} {f['package']:9} {f['vuln']:21} {f['reason'][:80]}")


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "help"
    st = Store()
    if cmd == "ingest":
        print(ingest.full_ingest(st))
        print("run:", analysis.triage(st))
        _print_findings(st)
    elif cmd == "triage":
        print("run:", analysis.triage(st))
        _print_findings(st)
    elif cmd == "whatif":
        variant = argv[1]
        print(ingest.reload_app_layer(st, variant))
        print("run:", analysis.triage(st, label=variant))
        _print_findings(st)
    elif cmd == "ask":
        reply, trace = Agent(st).ask(" ".join(argv[1:]))
        for t in trace:
            print(f"  tool> {t['tool']}({t['args']})")
        print(reply)
    elif cmd == "plan":
        print(json.dumps(planner.plan_remediation(st).as_dict(), indent=2))
    elif cmd == "remediate":
        res = verify.run_closed_loop(st)
        print(verify.render_report(res))
    elif cmd == "propose-pr":
        print(actions.propose_fix_pr(st))
    elif cmd == "freeze":
        r = frozen.record_whatif(st)
        print(f"what-if recorded: {len(r['flips'])} verdict flips, exposed {r['exposed_before']} -> {r['exposed_after']}")
    elif cmd == "record-agent":
        r = frozen.record_agent(st)
        print(f"agent conversation recorded: {len(r['turns'])} turns with {r['model']}; snapshot {r['snapshot']}")
    elif cmd == "seed":
        print(ingest.full_ingest(st))
        analysis.triage(st)
        res = verify.run_closed_loop(st)
        print("remediation:", res["status"], res["before"], "->", res["after"])
        r = frozen.record_whatif(st)
        print(f"what-if recorded: {len(r['flips'])} flips; snapshot {r['baseline_snapshot']}")
        try:
            a = frozen.record_agent(st)
            print(f"agent conversation recorded: {len(a['turns'])} turns with {a['model']}")
        except Exception as exc:  # no key / quota: the recorded conversation from the repo stays in place
            print(f"agent conversation not re-recorded ({exc.__class__.__name__}); keeping the existing recording")
    elif cmd == "approve":
        print(actions.execute(st, argv[1]))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
