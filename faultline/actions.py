"""Actions the agent can *propose*. A human approves; deterministic code re-derives the evidence and executes.

Approval is bound to evidence, not to a label: each Action stores a canonical payload (what, against which window,
with which hypothesis or owner numbers) and its SHA-256. On approval the payload is rebuilt from the current graph and
must hash to the same value, otherwise the action is STALE and must be re-proposed.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from . import analysis, config, experiments, guard, ingest, planner
from .graph import Store


def canonical(p: dict) -> str:
    return json.dumps(p, sort_keys=True, separators=(",", ":"))


def payload_hash(p: dict) -> str:
    return hashlib.sha256(canonical(p).encode()).hexdigest()


def _experiment_payload(st: Store, service: str) -> dict:
    w = ingest.latest_window(st, "baseline")
    hyp = experiments.hypothesis(st, w, service)
    return {"type": "experiment", "target": service, "baseline": w, "hypothesis": hyp["text"],
            "predicted": sorted(p["op"] for p in hyp["predicted"])}


def _fix_payload(st: Store, flags: list[str]) -> dict:
    w = ingest.latest_window(st)
    owners = analysis.critical_owners(st, w)[:3]
    return {"type": "fix", "flags": flags, "service": experiments.FIX_FLAGS[flags[0]][0], "baseline": w,
            "owners": [[analysis.label(o), o["per_request_ms"]] for o in owners]}


def _store(st: Store, payload: dict, title: str, rationale: str) -> dict:
    guard.require_writable("proposing an action")  # visitors of the public site read the recorded ledger only
    subject = {"experiment": payload.get("target"), "fix": " ".join(payload.get("flags", [])), "guardrail": payload.get("hop")}[payload["type"]]
    dup = st.q("MATCH (a:Action {type:$t, subject:$s, status:'proposed'}) RETURN a.id", {"t": payload["type"], "s": subject})
    if dup:
        return {"ok": True, "action": dup[0][0], "status": "proposed", "note": "already proposed; awaiting approval"}
    aid = f"act-{uuid.uuid4().hex[:8]}"
    st.q("CREATE (a:Action {id:$id, type:$t, subject:$s, status:'proposed', title:$title, rationale:$r, payload:$p, hash:$h, ts:$ts})",
         {"id": aid, "t": payload["type"], "s": subject, "title": title, "r": rationale, "p": canonical(payload), "h": payload_hash(payload), "ts": time.time()})
    return {"ok": True, "action": aid, "status": "proposed", "title": title, "hash": payload_hash(payload)[:12]}


def _guardrail_payload(st: Store, hop: str) -> dict | None:
    w = ingest.latest_window(st)
    g = next((x for x in planner.guardrails(st, w) if x["hop"] == hop and x["kind"] == "timeout"), None)
    if not g:
        return None
    return {"type": "guardrail", "hop": hop, "baseline": w, "timeout": g["value"], "evidence": g["evidence"], "priority": g["priority"]}


def guardrail_yaml(payload: dict) -> str:
    """The lesson as a concrete artifact: an Istio VirtualService timeout + bounded retries for the hop. Nothing here is
    applied to HotROD (it has no mesh); approval writes the file so the finding leaves the graph as a reviewable change."""
    src, dst = [x.strip() for x in payload["hop"].split("->")]
    ms = int(payload["timeout"].split()[0])
    return (f"# Faultline guardrail for {src} -> {dst}\n# evidence: {payload['evidence']}\n# window: {payload['baseline']}\n"
            f"apiVersion: networking.istio.io/v1\nkind: VirtualService\nmetadata:\n  name: {dst}-from-{src}\nspec:\n  hosts:\n  - {dst}\n"
            f"  http:\n  - timeout: {ms / 1000:.3f}s\n    retries:\n      attempts: 2\n      perTryTimeout: {ms / 2000:.3f}s\n"
            f"      retryOn: 5xx,reset,connect-failure\n    route:\n    - destination:\n        host: {dst}\n")


def propose_guardrail(st: Store, hop: str, rationale: str = "") -> dict:
    """Propose a timeout/retry policy for one hop, e.g. 'frontend -> route'. Approval writes the YAML under out/."""
    try:
        p = _guardrail_payload(st, hop)
        if not p:
            return {"ok": False, "error": f"no measured hop {hop!r}; hops come from guardrails()"}
        return _store(st, p, f"Add a {p['timeout']} timeout with bounded retries on {hop}", rationale or p["evidence"])
    except guard.ReadOnlyError as exc:
        return {"ok": False, "error": str(exc)}


def propose_experiment(st: Store, service: str, rationale: str = "") -> dict:
    if service not in experiments.SERVICES:
        return {"ok": False, "error": f"unknown service {service}; pausable: {sorted(experiments.SERVICES)}"}
    try:
        p = _experiment_payload(st, service)
        return _store(st, p, f"Pause {service} and compare with the graph's prediction", rationale or p["hypothesis"])
    except guard.ReadOnlyError as exc:
        return {"ok": False, "error": str(exc)}


def propose_fix(st: Store, flags: list[str], rationale: str = "") -> dict:
    if not flags or flags[0] not in experiments.FIX_FLAGS:
        return {"ok": False, "error": f"flags must start with one of {sorted(experiments.FIX_FLAGS)}"}
    try:
        p = _fix_payload(st, flags)
        return _store(st, p, f"Restart {p['service']} with {' '.join(flags)} and verify through the gates",
                      rationale or experiments.FIX_FLAGS[flags[0]][1])
    except guard.ReadOnlyError as exc:
        return {"ok": False, "error": str(exc)}


def list_actions(st: Store, status: str | None = None) -> list[dict]:
    _, rows = st.ro("MATCH (a:Action) WHERE $s IS NULL OR a.status = $s RETURN a.id, a.type, a.subject, a.status, a.title, a.rationale, a.hash, a.ts, a.result ORDER BY a.ts DESC",
                    {"s": status})
    return [{"id": r[0], "type": r[1], "subject": r[2], "status": r[3], "title": r[4], "rationale": r[5], "hash": (r[6] or "")[:12], "ts": r[7],
             "result": json.loads(r[8]) if r[8] else None} for r in rows]


def approve(st: Store, action_id: str, progress=print) -> dict:
    """Human approval. Re-derives the evidence; executes only if it still matches; records the outcome on the Action."""
    guard.require_writable("executing an action")
    rows = st.q("MATCH (a:Action {id:$id}) RETURN a.type, a.status, a.payload, a.hash", {"id": action_id})
    if not rows:
        return {"ok": False, "error": "no such action"}
    typ, status, payload_s, h = rows[0]
    if status != "proposed":
        return {"ok": False, "error": f"action is {status}, not proposed"}
    payload = json.loads(payload_s)
    if payload_hash(payload) != h:
        st.q("MATCH (a:Action {id:$id}) SET a.status = 'tampered'", {"id": action_id})
        return {"ok": False, "error": "stored payload does not match its hash"}
    fresh = (_experiment_payload(st, payload["target"]) if typ == "experiment" else _fix_payload(st, payload["flags"]) if typ == "fix"
             else _guardrail_payload(st, payload["hop"]))
    if payload_hash(fresh) != h:
        st.q("MATCH (a:Action {id:$id}) SET a.status = 'stale', a.stale_reason = $r", {"id": action_id, "r": canonical(fresh)})
        return {"ok": False, "error": "evidence changed since this was proposed (new window or different prediction); re-propose", "now": fresh}
    st.q("MATCH (a:Action {id:$id}) SET a.status = 'running'", {"id": action_id})
    try:
        if typ == "guardrail":
            out = config.ROOT / "out" / "guardrails"
            out.mkdir(parents=True, exist_ok=True)
            path = out / (payload["hop"].replace(" -> ", "_to_") + ".yaml")
            path.write_text(guardrail_yaml(payload))
            summary = {"written": str(path.relative_to(config.ROOT)), "timeout": payload["timeout"]}
        elif typ == "experiment":
            r = experiments.run_pause(st, payload["baseline"], payload["target"], progress=progress)
            summary = {"id": r["id"], "verdict": r["verdict"], "lesson": r["lesson"], "observed": r["observed"]}
            st.q("MATCH (a:Action {id:$id}), (e:Experiment {id:$eid}) CREATE (a)-[:EXECUTED_AS]->(e)", {"id": action_id, "eid": r["id"]})
        else:
            r = experiments.run_fix(st, payload["baseline"], payload["flags"], rationale="", progress=progress)
            summary = {"id": r["id"], "verdict": r["verdict"], "gates": r["gates"], "next_owner": r["next_owner"], "after": r["after"]}
            st.q("MATCH (a:Action {id:$id}), (f:Fix {id:$fid}) CREATE (a)-[:EXECUTED_AS]->(f)", {"id": action_id, "fid": r["id"]})
        st.q("MATCH (a:Action {id:$id}) SET a.status = 'done', a.result = $r, a.done_ts = $ts", {"id": action_id, "r": json.dumps(summary), "ts": time.time()})
        return {"ok": True, **summary}
    except Exception as exc:
        st.q("MATCH (a:Action {id:$id}) SET a.status = 'failed', a.result = $r", {"id": action_id, "r": json.dumps({"error": str(exc)[:300]})})
        raise


def reject(st: Store, action_id: str, why: str = "") -> dict:
    st.q("MATCH (a:Action {id:$id, status:'proposed'}) SET a.status = 'rejected', a.result = $r", {"id": action_id, "r": json.dumps({"why": why})})
    return {"ok": True, "action": action_id, "status": "rejected"}


def review_experiment(st: Store, experiment_id: str) -> dict:
    """Mark a REFUTED experiment as reviewed so the planner may schedule that target again (Monocle's review gate)."""
    st.q("MATCH (e:Experiment {id:$id}) SET e.reviewed = true", {"id": experiment_id})
    return {"ok": True, "experiment": experiment_id, "reviewed": True}
