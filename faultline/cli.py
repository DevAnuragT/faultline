"""faultline: load | ingest | report | reset"""
from __future__ import annotations

import argparse
import json
import shlex

from . import actions, analysis, experiments, ingest, load
from .graph import Store


def main() -> None:
    ap = argparse.ArgumentParser(prog="faultline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("load"); p.add_argument("--n", type=int, default=40); p.add_argument("--parallel", type=int, default=4)
    p = sub.add_parser("ingest"); p.add_argument("--label", default="baseline"); p.add_argument("--limit", type=int, default=200); p.add_argument("--lookback", default="1h")
    p = sub.add_parser("baseline", help="send traffic, then ingest exactly those traces as a window"); p.add_argument("--n", type=int, default=40); p.add_argument("--parallel", type=int, default=4); p.add_argument("--label", default="baseline")
    p = sub.add_parser("report"); p.add_argument("--window", default=None); p.add_argument("--json", action="store_true")
    p = sub.add_parser("blast"); p.add_argument("service"); p.add_argument("--window", default=None)
    p = sub.add_parser("experiment"); p.add_argument("service"); p.add_argument("--baseline", default=None); p.add_argument("--n", type=int, default=6)
    p = sub.add_parser("fix", help="apply one HotROD fix flag (-M | -D <dur> | -W <n>) and verify it in the graph"); p.add_argument("--flags", required=True, help="write it as --flags=-M or --flags=\"-W 100\" (the leading dash needs the = form)"); p.add_argument("--baseline", default=None)
    p = sub.add_parser("agent"); p.add_argument("question")
    p = sub.add_parser("actions"); p.add_argument("--status", default=None)
    p = sub.add_parser("approve"); p.add_argument("action_id")
    p = sub.add_parser("reject"); p.add_argument("action_id"); p.add_argument("--why", default="")
    sub.add_parser("record-agent", help="record the public site's agent conversation with the live model")
    p = sub.add_parser("init", help="write a starter faultline.yaml for YOUR system by probing Jaeger and docker compose"); p.add_argument("--jaeger", default="http://localhost:16686"); p.add_argument("--compose", default="docker-compose.yml"); p.add_argument("--out", default="faultline.yaml")
    sub.add_parser("profile", help="show the active profile: services, backend, actuators, code sources")
    sub.add_parser("revert")
    sub.add_parser("reset")
    a = ap.parse_args()
    if a.cmd == "init":
        from . import onboard
        print(onboard.write_starter(a.jaeger, a.compose, a.out)); return
    if a.cmd == "profile":
        from . import profile
        print(f"profile {profile.NAME} ({profile.PATH})\n backend {profile.BACKEND}; roots {profile.ROOT_SERVICES or 'auto'}; edge {profile.EDGE_SERVICES}")
        print(f" load: {len(profile.LOAD_URLS)} urls" if profile.LOAD_URLS else " load: none (observe-only ingest from live traffic)")
        print(f" pausable: {profile.PAUSABLE}; in-process: {profile.IN_PROCESS_OF}")
        for x in profile.ACTUATORS: print(f" actuator {x['id']!r}: {x['kind']} in {x['service']} -> {x['target']} {x['change']}")
        print(f" code: source_root={profile.SOURCE_ROOT or '-'} raw_base={profile.RAW_BASE or '-'} map={len(profile.CODE_MAP)} entries"); return
    if a.cmd == "load":
        load.run(a.n, a.parallel); return
    st = Store()
    if a.cmd == "reset":
        st.reset(); print("graph reset"); return
    if a.cmd == "baseline":
        import time
        start_us = int(time.time() * 1_000_000)
        load.run(a.n, a.parallel); time.sleep(4)
        r = ingest.ingest_window(st, a.label, start_us=start_us)
        print(f"{analysis.record_findings(st, r['window'], analysis.findings(st, r['window']))} findings recorded"); return
    if a.cmd == "ingest":
        r = ingest.ingest_window(st, a.label, limit=a.limit, lookback=a.lookback)
        n = analysis.record_findings(st, r["window"], analysis.findings(st, r["window"]))
        print(f"{n} findings recorded"); return
    if a.cmd == "agent":
        from .agent import Agent
        ag = Agent(st); reply, trace = ag.ask(a.question)
        for t in trace:
            print(f"  -> {t['tool']}({', '.join(f'{k}={v!r}' for k, v in t['args'].items())})")
        print(f"[{ag.model or 'offline'}]\n{reply}"); return
    if a.cmd == "actions":
        for x in actions.list_actions(st, a.status):
            print(f"{x['id']}  {x['status']:<9} {x['type']:<10} {x['subject']:<10} {x['title']}  " + (f"-> {x['result']}" if x["result"] else "")); return
    if a.cmd == "approve":
        print(json.dumps(actions.approve(st, a.action_id), indent=2)); return
    if a.cmd == "reject":
        print(actions.reject(st, a.action_id, a.why)); return
    if a.cmd == "record-agent":
        from . import frozen
        r = frozen.record_agent(st); print(f"recorded {len(r['turns'])} turns with {r['model']}"); return
    if a.cmd == "revert":
        experiments.revert_fixes(); return
    if a.cmd == "experiment":
        r = experiments.run_pause(st, a.baseline or ingest.latest_window(st, "baseline"), a.service, requests_n=a.n)
        print(json.dumps({k: r[k] for k in ("id", "hypothesis", "verdict", "rows", "unexpected_failures", "lesson")}, indent=2)); return
    if a.cmd == "fix":
        r = experiments.run_fix(st, a.baseline or ingest.latest_window(st, "baseline"), shlex.split(a.flags))
        print(json.dumps({k: r[k] for k in ("id", "service", "flags", "verdict", "next_owner")}, indent=2)); return
    w = a.window or ingest.latest_window(st)
    if a.cmd == "blast":
        print(json.dumps(analysis.blast_radius(st, w, a.service), indent=2)); return
    rep = {"window": w, "service_graph": analysis.service_graph(st, w), "critical_owners": analysis.critical_owners(st, w),
           "single_points_of_failure": analysis.single_points_of_failure(st, w), "fan_out": analysis.fan_out(st, w),
           "hangs": analysis.hangs(st, w), "root_health": analysis.root_health(st, w), "findings": analysis.findings(st, w)}
    if a.json:
        print(json.dumps(rep, indent=2)); return
    print(f"== window {w}")
    print("-- root operations (steady state)")
    for r in analysis.root_health(st, w):
        print(f"  {r['op']:<16} {r['traces']:>3} traces  failed {r['failed']:>3} ({r['fail_rate']:.0%})  p50 {r['p50_ms']:>7.0f} ms  p95 {r['p95_ms']:>7.0f} ms")
    print("-- service graph (per window)")
    for e in rep["service_graph"]:
        print(f"  {e['from']:>9} -> {e['to']:<13} {e['per_trace']:>5.1f}/req  p95 {e['p95_ms']:>7.1f} ms  errors {e['errors']}")
    print("-- critical path owners")
    for o in rep["critical_owners"][:8]:
        print(f"  {o['share_pct']:>5.1f}%  {analysis.label(o)}  ({o['per_request_ms']:.0f} ms/request, {o['errors']} errors)")
    print("-- single points of failure (betweenness)")
    for s in rep["single_points_of_failure"]:
        if s["betweenness"] > 0:
            print(f"  {s['service']:>9}  betweenness {s['betweenness']:.1f}  inbound calls {s['inbound_calls']}")
    print("-- findings")
    for f in rep["findings"]:
        print(f"  [{f['severity']:>6}] {f['kind']:<24} {f['detail']}")


if __name__ == "__main__":
    main()
