"""Act on the graph: fault experiments (pause a service) and fixes (restart a service with HotROD's fix flags).

Every experiment follows the chaos-engineering loop with the hypothesis computed from the graph:
  predict (blast radius from CALLS reachability) -> act (docker compose) -> observe (new Window) -> compare -> record.
"""
from __future__ import annotations

import subprocess
import time
import uuid

import yaml

from . import analysis, config, ingest, load
from .graph import Store

COMPOSE = config.ROOT / "docker-compose.yml"
OVERRIDE = config.ROOT / "docker-compose.fix.yml"
SERVICES = {"frontend", "customer", "driver", "route"}
FIX_FLAGS = {  # the knobs HotROD exposes, and the service each one lives in; the agent chooses among these, never free text
    "-M": ("customer", "disable the mutex serialising MySQL connections (lets customer queries run in parallel)"),
    "-D": ("customer", "MySQL query delay, e.g. -D 100ms (default 300ms)"),
    "-W": ("frontend", "size of the worker pool the frontend uses to call route, e.g. -W 100 (default 3)"),
}
OWNER_OF = {"mysql": "customer", "redis-manual": "driver"}  # in-process simulations live inside these containers


def _compose(*args: str, check: bool = True) -> str:
    files = ["-f", str(COMPOSE)] + (["-f", str(OVERRIDE)] if OVERRIDE.exists() else [])
    r = subprocess.run(["docker", "compose", *files, *args], capture_output=True, text=True, cwd=config.ROOT)
    if check and r.returncode:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {r.stderr.strip()[-400:]}")
    return r.stdout


def hypothesis(st: Store, window: str, service: str) -> dict:
    br = analysis.blast_radius(st, window, service)
    health = {h["op"]: h for h in analysis.root_health(st, window)}
    predicted = []
    for r in br["root_operations"]:
        h = health.get(r["op"], {})
        predicted.append({"op": r["op"], "service": r["service"], "baseline_fail_rate": h.get("fail_rate", 0.0),
                          "baseline_p50_ms": h.get("p50_ms", 0.0), "predicted": "fails"})
    unaffected = [h["op"] for h in health.values() if h["op"] not in {p["op"] for p in predicted}]
    text = (f"If {service} stops, {', '.join(p['op'] for p in predicted) or 'nothing recorded'} fail(s) "
            f"({', '.join(br['dependent_services'])} depend on it); {', '.join(unaffected) or 'no other root operation'} unaffected.")
    return {"target": service, "text": text, "predicted": predicted, "unaffected": unaffected, "dependent_services": br["dependent_services"]}


def run_pause(st: Store, baseline: str, service: str, requests_n: int = 6, parallel: int = 3, progress=print) -> dict:
    """Pause one container, send traffic, unpause, ingest what happened, and compare with the graph's prediction."""
    if service not in SERVICES:
        raise ValueError(f"unknown service {service}; one of {sorted(SERVICES)}")
    hyp = hypothesis(st, baseline, service)
    progress(f"hypothesis: {hyp['text']}")
    exp_id = f"exp-{uuid.uuid4().hex[:8]}"
    start_us = int(time.time() * 1_000_000)
    _compose("pause", service)
    progress(f"paused {service}; sending {requests_n} requests ({parallel} in parallel)")
    try:
        observed_load = load.run(requests_n, parallel, progress=progress)
    finally:
        _compose("unpause", service)
        progress(f"unpaused {service}")
    time.sleep(4)  # let the SDK flush spans to Jaeger
    w = ingest.ingest_window(st, f"pause-{service}", start_us=start_us, progress=progress)["window"]
    base_p50 = {p["op"]: p["baseline_p50_ms"] for p in hyp["predicted"]} | {op: h["p50_ms"] for op, h in
                                                                            ((h["op"], h) for h in analysis.root_health(st, baseline))}
    health = {h["op"]: h for h in analysis.root_health(st, w, baseline_p50=base_p50)}
    rows = []
    for p in hyp["predicted"]:
        h = health.get(p["op"])
        failed = bool(h and h["fail_rate"] >= 0.5)
        rows.append({**p, "observed_fail_rate": h["fail_rate"] if h else None, "observed_p50_ms": h["p50_ms"] if h else None,
                     "failed_fast": bool(h and h["p50_ms"] < 5 * max(p["baseline_p50_ms"], 1)), "confirmed": failed})
    unexpected = [op for op in hyp["unaffected"] if health.get(op, {}).get("fail_rate", 0) >= 0.5]
    confirmed = all(r["confirmed"] for r in rows) and not unexpected
    verdict = "CONFIRMED" if confirmed else "REFUTED"
    hung = [r for r in rows if r["confirmed"] and not r["failed_fast"]]
    lesson = (f"{', '.join(r['op'] for r in hung)} did not fail fast: p50 {hung[0]['observed_p50_ms']:.0f} ms vs baseline "
              f"{hung[0]['baseline_p50_ms']:.0f} ms; a timeout or circuit breaker on the hop into {service} would turn a hang into an error"
              if hung else "affected operations failed fast")
    result = {"id": exp_id, "kind": "pause", "target": service, "hypothesis": hyp["text"], "baseline": baseline, "observed": w,
              "verdict": verdict, "rows": rows, "unexpected_failures": unexpected, "lesson": lesson, "load": observed_load}
    st.q("""MATCH (b:Window {id:$b}), (o:Window {id:$o}), (s:Service {name:$svc})
            CREATE (e:Experiment {id:$id, kind:'pause', target:$svc, hypothesis:$h, verdict:$v, lesson:$lesson, ts:$ts})
            CREATE (e)-[:TARGETS]->(s) CREATE (e)-[:BASELINE]->(b) CREATE (e)-[:OBSERVED]->(o)
            WITH e UNWIND $rows AS r MATCH (op:Operation {key: r.service + '::' + r.op})
            CREATE (e)-[:PREDICTED_IMPACT {outcome:'fails'}]->(op)
            CREATE (e)-[:ACTUAL_IMPACT {fail_rate:r.observed_fail_rate, p50_ms:r.observed_p50_ms, failed_fast:r.failed_fast}]->(op)""",
         {"b": baseline, "o": w, "svc": service, "id": exp_id, "h": hyp["text"], "v": verdict, "lesson": lesson, "ts": time.time(), "rows": rows})
    progress(f"{verdict}: {lesson}")
    return result


def _write_override(service: str, flags: list[str]) -> list[str]:
    """Accumulate fixes: the override file is the current state of the stack, one service at a time."""
    base = yaml.safe_load(COMPOSE.read_text())
    over = yaml.safe_load(OVERRIDE.read_text()) if OVERRIDE.exists() else {"services": {}}
    current = over["services"].get(service, {}).get("command") or list(base["services"][service]["command"])
    cmd = list(current) + flags
    over["services"][service] = {"command": cmd}
    OVERRIDE.write_text(yaml.safe_dump(over))
    return cmd


def run_fix(st: Store, baseline: str, flags: list[str], rationale: str = "", requests_n: int = 40, parallel: int = 4,
            min_gain_pct: float = 20.0, progress=print) -> dict:
    """Apply one HotROD fix flag to the service it belongs to, re-measure, and accept only if the graph shows the gain
    where it was claimed (the target's critical-path time drops), the endpoint is faster, and nothing got worse."""
    if not flags or flags[0] not in FIX_FLAGS:
        raise ValueError(f"flags must start with one of {sorted(FIX_FLAGS)}")
    service = FIX_FLAGS[flags[0]][0]
    fix_id = f"fix-{uuid.uuid4().hex[:8]}"
    before_health = {h["op"]: h for h in analysis.root_health(st, baseline)}
    before_owners = analysis.critical_owners(st, baseline)
    cmd = _write_override(service, flags)
    _compose("up", "-d", "--no-deps", "--force-recreate", service)
    progress(f"restarted {service} as: {' '.join(cmd)}; warming up")
    time.sleep(5)
    start_us = int(time.time() * 1_000_000)
    load.run(requests_n, parallel, progress=progress)
    time.sleep(4)
    w = ingest.ingest_window(st, f"fix-{service}", start_us=start_us, progress=progress)["window"]
    after_health = {h["op"]: h for h in analysis.root_health(st, w, baseline_p50={op: h["p50_ms"] for op, h in before_health.items()})}
    after_owners = analysis.critical_owners(st, w)

    def owned_by(owners: list[dict]) -> float:  # critical-path ms per request owned by the fixed service and the stores inside it
        return sum(o["per_request_ms"] for o in owners if OWNER_OF.get(o["service"], o["service"]) == service)

    gates = []
    for op, b in before_health.items():
        a = after_health.get(op)
        if not a:
            gates.append({"name": f"{op} still served", "passed": False, "detail": "no traces after the change"}); continue
        gain = 100.0 * (b["p50_ms"] - a["p50_ms"]) / max(b["p50_ms"], 1)
        gates.append({"name": f"{op} median improves >= {min_gain_pct:.0f}%", "passed": gain >= min_gain_pct,
                      "detail": f"p50 {b['p50_ms']:.0f} -> {a['p50_ms']:.0f} ms ({gain:+.0f}%)"})
        gates.append({"name": f"{op} tail not worse", "passed": a["p95_ms"] <= b["p95_ms"] * 1.05,
                      "detail": f"p95 {b['p95_ms']:.0f} -> {a['p95_ms']:.0f} ms"})
        gates.append({"name": f"{op} failure rate not worse", "passed": a["fail_rate"] <= b["fail_rate"] + 0.02,
                      "detail": f"{b['fail_rate']:.0%} -> {a['fail_rate']:.0%}"})
    ob, oa = owned_by(before_owners), owned_by(after_owners)
    gates.append({"name": f"{service}'s critical-path time drops >= 25%", "passed": ob > 0 and (ob - oa) / ob >= 0.25,
                  "detail": f"{ob:.0f} -> {oa:.0f} ms per request on the critical path"})
    verdict = "VERIFIED" if all(g["passed"] for g in gates) else "REJECTED"
    nxt = after_owners[0] if after_owners else None
    next_owner = f"{analysis.label(nxt)} now owns {nxt['share_pct']}% ({nxt['per_request_ms']:.0f} ms/request)" if nxt else ""
    result = {"id": fix_id, "service": service, "flags": flags, "command": cmd, "rationale": rationale, "before": baseline, "after": w,
              "gates": gates, "verdict": verdict, "owners_before": before_owners[:5], "owners_after": after_owners[:5], "next_owner": next_owner}
    st.q("""MATCH (b:Window {id:$b}), (a:Window {id:$a}), (s:Service {name:$svc})
            CREATE (f:Fix {id:$id, service:$svc, flags:$flags, rationale:$r, verdict:$v, gates:$gates, next_owner:$nxt, ts:$ts})
            CREATE (f)-[:TARGETS]->(s) CREATE (f)-[:BEFORE]->(b) CREATE (f)-[:AFTER]->(a)""",
         {"b": baseline, "a": w, "svc": service, "id": fix_id, "flags": " ".join(flags), "r": rationale, "v": verdict, "nxt": next_owner,
          "gates": [f"{'PASS' if g['passed'] else 'FAIL'} {g['name']}: {g['detail']}" for g in gates], "ts": time.time()})
    progress(verdict)
    for g in gates:
        progress(f"  {'PASS' if g['passed'] else 'FAIL'}  {g['name']}: {g['detail']}")
    progress(f"  next: {next_owner}")
    return result


def revert_fixes(progress=print) -> None:
    if OVERRIDE.exists():
        OVERRIDE.unlink()
    _compose("up", "-d", "--force-recreate", *sorted(SERVICES))
    progress("all services back to stock flags")
