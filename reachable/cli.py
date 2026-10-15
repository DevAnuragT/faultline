"""Command line: python -m reachable.cli {ingest|triage|whatif <variant>|ask "<question>"|approve <action>}"""
from __future__ import annotations

import sys

from . import actions, analysis, ingest
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
    elif cmd == "approve":
        print(actions.execute(st, argv[1]))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
