"""Act on the graph: fault experiments (pause a service) and fixes (apply an actuator the profile declares).

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

from . import profile

COMPOSE = profile.COMPOSE_FILE
OVERRIDE = profile.OVERRIDE_FILE
SERVICES = set(profile.PAUSABLE)                       # services Faultline may pause (declared in the profile)
OWNER_OF = profile.IN_PROCESS_OF                       # in-process stores -> the service they live in
# Actuators come from the profile: a remedy class the graph may find -> a change THIS environment can make.
# FIX_FLAGS keeps the historical shape {first token of actuator id: (target service, what)}; ACTUATORS is keyed by (kind, service).
FIX_FLAGS = {a["id"].split()[0]: (a["target"], a["what"]) for a in profile.ACTUATORS}
ACTUATORS = {(a["kind"], a["service"]): {"flags": a["id"].split(), "service": a["target"], "what": a["what"]} for a in profile.ACTUATORS}


def actuator(flags: list[str]) -> dict | None:
    """Resolve an actuator by its id ("-W 100") or by its first token ("-W")."""
    key = " ".join(flags)
    if key in profile.ACTUATOR_BY_ID:
        return profile.ACTUATOR_BY_ID[key]
    return next((a for a in profile.ACTUATORS if a["id"].split()[0] == flags[0]), None)


def _k8s(*args: str, check: bool = True) -> str:
    ns = profile.K8S.get("namespace"); ctx = profile.K8S.get("context")
    cmd = ["kubectl"] + (["-n", ns] if ns else []) + (["--context", ctx] if ctx else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip()[-300:]}")
    return r.stdout


_replicas: dict[str, str] = {}


def pause(service: str) -> None:
    name = profile.runtime_name(service)
    if profile.BACKEND == "compose":
        _compose("pause", name)
    elif profile.BACKEND == "kubernetes":
        _replicas[service] = _k8s("get", f"deploy/{name}", "-o", "jsonpath={.spec.replicas}").strip() or "1"
        _k8s("scale", f"deploy/{name}", "--replicas=0")
    else:
        raise RuntimeError("this profile is observe-only (environment.backend: none): experiments are disabled")


def unpause(service: str) -> None:
    name = profile.runtime_name(service)
    if profile.BACKEND == "compose":
        _compose("unpause", name)
    elif profile.BACKEND == "kubernetes":
        _k8s("scale", f"deploy/{name}", f"--replicas={_replicas.get(service, '1')}")


def _compose(*args: str, check: bool = True) -> str:
    files = ["-f", str(COMPOSE)] + (["-f", str(OVERRIDE)] if OVERRIDE.exists() else [])
    r = subprocess.run(["docker", "compose", *files, *args], capture_output=True, text=True, cwd=COMPOSE.parent)
    if check and r.returncode:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {r.stderr.strip()[-400:]}")
    return r.stdout


def _calls_per_trace(st: Store, window: str) -> dict[str, dict]:
    """Work per service in a window: its spans per trace and their error rate (simulated stores emit client-kind spans,
    so every span counts; the comparison is always baseline against experiment for the same service)."""
    _, n = st.ro("MATCH (t:Trace {window:$w}) RETURN count(t)", {"w": window})
    traces = max(n[0][0] if n else 0, 1)
    _, rows = st.ro("MATCH (s:Span {window:$w}) RETURN s.service, count(s), sum(CASE WHEN s.error THEN 1 ELSE 0 END)", {"w": window})
    return {r[0]: {"calls_per_trace": (r[1] or 0) / traces, "error_rate": ((r[2] or 0) / r[1]) if r[1] else 0.0} for r in rows}


def _call_order(st: Store, window: str) -> dict[str, float]:
    """Median offset (ms from trace start) at which each service's first span starts: the request's call order."""
    _, rows = st.ro("""MATCH (s:Span {window:$w})-[:OF]->(t:Trace) WITH t, s.service AS svc, min(s.start_us) AS first
                       RETURN svc, percentileDisc(first - t.start_us, 0.5) / 1000.0""", {"w": window})
    return {r[0]: r[1] for r in rows}


def hypothesis(st: Store, window: str, service: str) -> dict:
    """What the graph predicts if `service` stops. Three parts, each checkable against the experiment window:
    (1) which root operations fail; (2) which services keep answering (called before the first call into the target,
    by the baseline's call order); (3) which services are starved (only ever called after it) and should see almost no calls."""
    br = analysis.blast_radius(st, window, service)
    health = {h["op"]: h for h in analysis.root_health(st, window)}
    predicted = []
    for r in br["root_operations"]:
        h = health.get(r["op"], {})
        predicted.append({"op": r["op"], "service": r["service"], "baseline_fail_rate": h.get("fail_rate", 0.0),
                          "baseline_p50_ms": h.get("p50_ms", 0.0), "predicted": "fails"})
    unaffected = [h["op"] for h in health.values() if h["op"] not in {p["op"] for p in predicted}]
    order = _call_order(st, window)
    calls = _calls_per_trace(st, window)
    t0 = order.get(service, 0.0)
    from . import config
    keep = sorted(s for s, t in order.items() if s != service and s not in profile.EDGE_SERVICES and t < t0 and calls.get(s, {}).get("calls_per_trace", 0) > 0)
    frozen = sorted(s for s, owner in OWNER_OF.items() if owner == service and calls.get(s, {}).get("calls_per_trace", 0) > 0)
    starved = sorted(s for s, t in order.items() if s != service and s not in profile.EDGE_SERVICES and t >= t0 and s not in br["dependent_services"]
                     and s not in frozen and calls.get(s, {}).get("calls_per_trace", 0) > 0)
    text = (f"If {service} stops, {', '.join(p['op'] for p in predicted) or 'nothing recorded'} fail(s) "
            f"({', '.join(br['dependent_services'])} depend on it); "
            + (f"{', '.join(keep)} keep answering (called before {service}); " if keep else "")
            + (f"{', '.join(starved)} get almost no calls (only called after {service}); " if starved else "")
            + (f"{', '.join(frozen)} freeze with it (in-process) and drain their backlog when it resumes unless callers cancel; " if frozen else "")
            + (f"{', '.join(unaffected)} unaffected." if unaffected else "no other root operation."))
    return {"target": service, "text": text, "predicted": predicted, "unaffected": unaffected, "dependent_services": br["dependent_services"],
            "keep_answering": keep, "starved": starved, "frozen_with": frozen, "baseline_calls": {s: calls.get(s, {}) for s in keep + starved + frozen}}


def run_pause(st: Store, baseline: str, service: str, requests_n: int = 6, parallel: int = 3, progress=print) -> dict:
    """Pause one container, send traffic, unpause, ingest what happened, and compare with the graph's prediction."""
    if service not in SERVICES:
        raise ValueError(f"unknown service {service}; the profile allows pausing {sorted(SERVICES)}")
    hyp = hypothesis(st, baseline, service)
    progress(f"hypothesis: {hyp['text']}")
    exp_id = f"exp-{uuid.uuid4().hex[:8]}"
    start_us = int(time.time() * 1_000_000)
    pause(service)
    progress(f"paused {service}; sending {requests_n} requests ({parallel} in parallel)")
    try:
        observed_load = load.run(requests_n, parallel, progress=progress)
    finally:
        load_end_us = int(time.time() * 1_000_000)  # every request has been sent and has given up by now
        unpause(service)
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
    after_calls = _calls_per_trace(st, w)
    parts = [{"part": "failing operations fail", "ok": all(r["confirmed"] for r in rows) and not unexpected,
              "detail": "; ".join(f"{r['op']}: {r['observed_fail_rate']:.0%} failed" for r in rows) + (f"; unexpected: {unexpected}" if unexpected else "")}]
    for svc in hyp["keep_answering"]:
        b, a = hyp["baseline_calls"].get(svc, {}), after_calls.get(svc, {"calls_per_trace": 0.0, "error_rate": 1.0})
        ok = a["calls_per_trace"] >= 0.5 * b.get("calls_per_trace", 0) and a["error_rate"] <= b.get("error_rate", 0) + 0.2
        parts.append({"part": f"{svc} keeps answering", "ok": ok,
                      "detail": f"{a['calls_per_trace']:.1f} calls/request (baseline {b.get('calls_per_trace', 0):.1f}), error rate {a['error_rate']:.0%} (baseline {b.get('error_rate', 0):.0%})"})
    for svc in hyp["starved"]:
        b, a = hyp["baseline_calls"].get(svc, {}), after_calls.get(svc, {"calls_per_trace": 0.0, "error_rate": 0.0})
        ok = a["calls_per_trace"] <= 0.2 * max(b.get("calls_per_trace", 0), 0.01)
        parts.append({"part": f"{svc} is starved", "ok": ok, "detail": f"{a['calls_per_trace']:.1f} calls/request (baseline {b.get('calls_per_trace', 0):.1f})"})
    backlog = []
    for svc in hyp.get("frozen_with", []) + hyp["starved"]:
        _, late = st.ro("MATCH (s:Span {window:$w, service:$s}) WHERE s.start_us > $t RETURN count(s)", {"w": w, "s": svc, "t": load_end_us})
        if late and late[0][0]:
            backlog.append(f"{late[0][0]} calls into {svc} ran after {service} resumed")
    confirmed = all(p["ok"] for p in parts)
    verdict = "CONFIRMED" if confirmed else "REFUTED"
    hung = [r for r in rows if r["confirmed"] and not r["failed_fast"]]
    lesson = (f"{', '.join(r['op'] for r in hung)} did not fail fast: p50 {hung[0]['observed_p50_ms']:.0f} ms vs baseline "
              f"{hung[0]['baseline_p50_ms']:.0f} ms; a timeout or circuit breaker on the hop into {service} would turn a hang into an error"
              if hung else "affected operations failed fast")
    if backlog:
        lesson += ". Backlog: " + "; ".join(backlog) + " after every caller had already given up: no deadline reached the callee, so the work ran late for nobody"
    if not confirmed:
        lesson = "prediction refuted: " + "; ".join(f"{p['part']} ({p['detail']})" for p in parts if not p["ok"]) + ". " + lesson
    result = {"id": exp_id, "kind": "pause", "target": service, "hypothesis": hyp["text"], "baseline": baseline, "observed": w,
              "verdict": verdict, "rows": rows, "parts": parts, "backlog": backlog, "unexpected_failures": unexpected, "lesson": lesson, "load": observed_load}
    st.q("""MATCH (b:Window {id:$b}), (o:Window {id:$o}), (s:Service {name:$svc})
            CREATE (e:Experiment {id:$id, kind:'pause', target:$svc, hypothesis:$h, verdict:$v, lesson:$lesson, parts:$parts, ts:$ts})
            CREATE (e)-[:TARGETS]->(s) CREATE (e)-[:BASELINE]->(b) CREATE (e)-[:OBSERVED]->(o)
            WITH e UNWIND $rows AS r MATCH (op:Operation {key: r.service + '::' + r.op})
            CREATE (e)-[:PREDICTED_IMPACT {outcome:'fails'}]->(op)
            CREATE (e)-[:ACTUAL_IMPACT {fail_rate:r.observed_fail_rate, p50_ms:r.observed_p50_ms, failed_fast:r.failed_fast}]->(op)""",
         {"b": baseline, "o": w, "svc": service, "id": exp_id, "h": hyp["text"], "v": verdict, "lesson": lesson, "ts": time.time(), "rows": rows,
          "parts": [f"{'PASS' if p['ok'] else 'FAIL'} {p['part']}: {p['detail']}" for p in parts]})
    progress(f"{verdict}: {lesson}")
    return result


def _apply_change(service: str, change: dict) -> str:
    """Apply an actuator's change to a service. compose: args appended to the command or env set, through an override file
    that is the current state of the stack; kubernetes: env on the deployment. Returns a description of what runs now."""
    name = profile.runtime_name(service)
    if profile.BACKEND == "compose":
        base = yaml.safe_load(COMPOSE.read_text())
        over = yaml.safe_load(OVERRIDE.read_text()) if OVERRIDE.exists() else {"services": {}}
        svc = over["services"].setdefault(name, {})
        if change.get("args"):
            cmd = list(svc.get("command") or base["services"][name].get("command") or [])
            svc["command"] = cmd + list(change["args"])
        if change.get("env"):
            env = dict(svc.get("environment") or {}); env.update({k: str(v) for k, v in change["env"].items()}); svc["environment"] = env
        OVERRIDE.write_text(yaml.safe_dump(over))
        _compose("up", "-d", "--no-deps", "--force-recreate", name)
        return " ".join(svc.get("command") or []) + (f" env {svc.get('environment')}" if svc.get("environment") else "")
    if profile.BACKEND == "kubernetes":
        if change.get("args"):
            raise RuntimeError("args changes need a compose backend; on kubernetes declare the change as env or replicas")
        if change.get("env"):
            _k8s("set", "env", f"deploy/{name}", *[f"{k}={v}" for k, v in change["env"].items()])
        if change.get("replicas"):
            _k8s("scale", f"deploy/{name}", f"--replicas={change['replicas']}")
        _k8s("rollout", "status", f"deploy/{name}", "--timeout=120s", check=False)
        return str(change)
    raise RuntimeError("this profile is observe-only (environment.backend: none): fixes are disabled")


def _remove_change(service: str, change: dict) -> None:
    name = profile.runtime_name(service)
    if profile.BACKEND == "compose":
        over = yaml.safe_load(OVERRIDE.read_text()) if OVERRIDE.exists() else {"services": {}}
        svc = over["services"].get(name, {})
        if change.get("args") and svc.get("command"):
            cmd, args = svc["command"], list(change["args"]); n = len(args)
            for i in range(len(cmd) - n, -1, -1):
                if cmd[i:i + n] == args:
                    del cmd[i:i + n]; break
        if change.get("env") and svc.get("environment"):
            for k in change["env"]:
                svc["environment"].pop(k, None)
        OVERRIDE.write_text(yaml.safe_dump(over))
        _compose("up", "-d", "--no-deps", "--force-recreate", name, check=False)
    elif profile.BACKEND == "kubernetes":
        if change.get("env"):
            _k8s("set", "env", f"deploy/{name}", *[f"{k}-" for k in change["env"]], check=False)
        if change.get("replicas"):
            _k8s("scale", f"deploy/{name}", f"--replicas={_replicas.get(service, '1')}", check=False)


def _write_override(service: str, flags: list[str]) -> list[str]:  # kept for callers that think in flags
    _apply_change(service, {"args": flags}); return flags


def _remove_override(service: str, flags: list[str]) -> None:
    _remove_change(service, {"args": flags})


def run_fix(st: Store, baseline: str, flags: list[str], rationale: str = "", requests_n: int = 40, parallel: int = 4,
            min_gain_pct: float = 20.0, progress=print) -> dict:
    """Apply one actuator from the profile (by id, e.g. "-M" or "-W 100") to its target service, re-measure, and accept only
    if the graph shows the gain where it was claimed (the target's critical-path time drops), the endpoint is faster, and
    nothing got worse."""
    act = actuator(flags) if flags else None
    if not act:
        raise ValueError(f"unknown actuator {' '.join(flags)!r}; the profile declares {sorted(profile.ACTUATOR_BY_ID)}")
    flags = act["id"].split()
    service = act["target"]
    fix_id = f"fix-{uuid.uuid4().hex[:8]}"
    before_health = {h["op"]: h for h in analysis.root_health(st, baseline)}
    before_owners = analysis.critical_owners(st, baseline)
    try:
        now = _apply_change(service, act["change"])
        progress(f"applied {act['id']} to {service}: {now}; warming up")
        time.sleep(5)
        start_us = int(time.time() * 1_000_000)
        load.run(requests_n, parallel, progress=progress)
        time.sleep(4)
        w = ingest.ingest_window(st, f"fix-{service}", start_us=start_us, progress=progress)["window"]
    except BaseException:  # anything going wrong between restart and measurement must not leave the change in place
        _remove_change(service, act["change"])
        progress(f"rolled back after an error: {service} restored without {act['id']}")
        raise
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
        gates.append({"name": f"{op} failures not worse (tolerance: one request or 5%)",
                      "passed": a["failed"] <= b["failed"] + 1 and a["fail_rate"] <= b["fail_rate"] + 0.05,
                      "detail": f"{b['failed']}/{b['traces']} -> {a['failed']}/{a['traces']} failed"})
    ob, oa = owned_by(before_owners), owned_by(after_owners)
    gates.append({"name": f"{service}'s critical-path time drops >= 25%", "passed": ob > 0 and (ob - oa) / ob >= 0.25,
                  "detail": f"{ob:.0f} -> {oa:.0f} ms per request on the critical path"})
    verdict = "VERIFIED" if all(g["passed"] for g in gates) else "REJECTED"
    if verdict == "REJECTED":  # closed loop: a rejected change does not stay in the system, and its window is not steady state
        _remove_change(service, act["change"])
        st.q("MATCH (x:Window {id:$w}) SET x.steady = false", {"w": w})
        progress(f"rolled back: {service} restored without {act['id']}")
    nxt = after_owners[0] if after_owners else None
    next_owner = f"{analysis.label(nxt)} now owns {nxt['share_pct']}% ({nxt['per_request_ms']:.0f} ms/request)" if nxt else ""
    result = {"id": fix_id, "service": service, "flags": flags, "command": now, "rationale": rationale, "before": baseline, "after": w,
              "gates": gates, "verdict": verdict, "rolled_back": verdict == "REJECTED", "owners_before": before_owners[:5], "owners_after": after_owners[:5], "next_owner": next_owner}
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
    if profile.BACKEND == "compose":
        if OVERRIDE.exists():
            OVERRIDE.unlink()
        _compose("up", "-d", "--force-recreate", *sorted(profile.runtime_name(s) for s in SERVICES))
        progress("all services back to their stock configuration")
    else:
        progress("revert is only automatic on the compose backend; undo kubernetes changes with the actuator ids you applied")
