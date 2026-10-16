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
    subject = {"experiment": payload.get("target"), "fix": " ".join(payload.get("flags", [])), "guardrail": payload.get("hop"),
               "issue": payload.get("window"), "pr": payload.get("subject")}[payload["type"]]
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
             else _guardrail_payload(st, payload["hop"]) if typ == "guardrail" else _issue_payload(st, payload["window"]) if typ == "issue"
             else _pr_payload(st, payload["subject"]))
    if payload_hash(fresh) != h:
        st.q("MATCH (a:Action {id:$id}) SET a.status = 'stale', a.stale_reason = $r", {"id": action_id, "r": canonical(fresh)})
        return {"ok": False, "error": "evidence changed since this was proposed (new window or different prediction); re-propose", "now": fresh}
    st.q("MATCH (a:Action {id:$id}) SET a.status = 'running'", {"id": action_id})
    try:
        if typ == "issue":
            summary = _execute_issue(st, payload)
        elif typ == "pr":
            summary = _execute_pr(st, payload)
        elif typ == "guardrail":
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


# ------------------------------------------------------------------------------------------ incident issues and draft PRs
import base64
import shutil
import subprocess

from . import code as codemap

def _out():
    return config.ROOT / "out"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def incident_report(inc: dict) -> tuple[str, str]:
    o = inc.get("owner") or {}
    c = (o.get("code") or {})
    title = (f"[{inc['kind']}] {inc['endpoint']}: {inc['fail_rate'][1]:.0%} failed, p95 {inc['p95_ms'][1]:.0f} ms "
             f"(baseline {inc['fail_rate'][0]:.0%}, {inc['p95_ms'][0]:.0f} ms) in window {inc['window']}")
    body = (f"**Window** `{inc['window']}` ({inc['label']}), {inc['traces']} traces, against steady state `{inc['baseline']}`.\n\n"
            f"| | baseline | this window |\n|---|---|---|\n| failure rate | {inc['fail_rate'][0]:.0%} | {inc['fail_rate'][1]:.0%} |\n"
            f"| p50 | {inc['p50_ms'][0]:.0f} ms | {inc['p50_ms'][1]:.0f} ms |\n| p95 | {inc['p95_ms'][0]:.0f} ms | {inc['p95_ms'][1]:.0f} ms |\n\n"
            + (f"**What moved:** `{analysis.label(o)}` went from {o['baseline_per_request_ms']:.0f} to {o['per_request_ms']:.0f} ms per request on the critical path.\n\n" if o else "")
            + (f"**Where to look:** {inc['hop']}" + (f"; code `{c.get('location') or c.get('file')}`" + (f" ({c['function']})" if c.get("function") else "") + (f" {c['url']}" if c.get("url") else "") if c else "") + ".\n\n" if inc.get("hop") else "")
            + (f"**Worst request:** trace `{inc['worst_trace']['id']}`, {inc['worst_trace']['duration_ms']:.0f} ms, status {inc['worst_trace']['status'] or 'none'}.\n\n" if inc.get("worst_trace") else "")
            + "_Generated by Faultline from the trace graph; every number is a query over the two windows named above._\n")
    return title, body


def _issue_payload(st: Store, window: str) -> dict | None:
    inc = analysis.incident(st, window)
    if not inc:
        return None
    title, body = incident_report(inc)
    return {"type": "issue", "window": window, "baseline": inc["baseline"], "endpoint": inc["endpoint"], "kind": inc["kind"],
            "fail_rate": inc["fail_rate"], "p95_ms": inc["p95_ms"], "title": title, "body_sha": _sha(body)}


def propose_issue(st: Store, window: str, rationale: str = "") -> dict:
    """Propose an incident issue for a window that reads as an incident against steady state."""
    try:
        p = _issue_payload(st, window)
        if not p:
            return {"ok": False, "error": f"window {window} is not an incident against the current steady state (or is the steady state itself)"}
        return _store(st, p, f"File incident: {p['title']}", rationale or "failure rate or p95 regressed against the steady-state window")
    except guard.ReadOnlyError as exc:
        return {"ok": False, "error": str(exc)}


def _fix_files(st: Store, fix_id: str) -> dict[str, str] | None:
    rows = st.q("MATCH (f:Fix {id:$id})-[:BEFORE]->(b) MATCH (f)-[:AFTER]->(a) RETURN f.service, f.flags, f.verdict, f.gates, f.next_owner, b.id, a.id", {"id": fix_id})
    if not rows or rows[0][2] != "VERIFIED":
        return None
    service, flags, _, gates, nxt, before, after = rows[0]
    import yaml
    base = yaml.safe_load((config.ROOT / "docker-compose.yml").read_text())
    cmd = list(base["services"][service]["command"]) + flags.split()
    override = yaml.safe_dump({"services": {service: {"command": cmd}}})
    ptr = codemap.flag_pointer(flags)
    report = (f"# {flags} on {service}: VERIFIED\n\nWindows `{before}` -> `{after}`.\n\n" + "\n".join(f"- {g}" for g in gates)
              + f"\n\nNext owner after this change: {nxt}\n" + (f"\nKnob in source: `{ptr['file']}` ({ptr['symbol']}) {ptr['url']}\n" if ptr else ""))
    return {"deploy/docker-compose.fix.yml": override, f"docs/changes/{fix_id}.md": report}


def _guardrail_files(st: Store, action_id: str) -> dict[str, str] | None:
    rows = st.q("MATCH (a:Action {id:$id, type:'guardrail', status:'done'}) RETURN a.payload", {"id": action_id})
    if not rows:
        return None
    payload = json.loads(rows[0][0])
    name = payload["hop"].replace(" -> ", "_to_")
    report = (f"# Timeout on {payload['hop']}\n\n{payload['evidence']}.\n\nProposed: {payload['timeout']} timeout with two bounded retries "
              f"(Istio VirtualService, see `deploy/guardrails/{name}.yaml`). Window `{payload['baseline']}`.\n")
    return {f"deploy/guardrails/{name}.yaml": guardrail_yaml(payload), f"docs/changes/{action_id}.md": report}


def _experiment_files(st: Store, exp_id: str) -> dict[str, str] | None:
    rows = st.q("MATCH (e:Experiment {id:$id})-[:OBSERVED]->(w) RETURN w.id", {"id": exp_id})
    if not rows:
        return None
    inc = analysis.incident(st, rows[0][0])
    if not inc:
        return None
    title, body = incident_report(inc)
    return {f"docs/incidents/{exp_id}.md": f"# {title}\n\n{body}"}


def pr_files(st: Store, subject: str) -> tuple[str, dict[str, str]] | None:
    for kind, fn in (("fix", _fix_files), ("guardrail", _guardrail_files), ("experiment", _experiment_files)):
        files = fn(st, subject)
        if files:
            return kind, files
    return None


def _pr_payload(st: Store, subject: str) -> dict | None:
    got = pr_files(st, subject)
    if not got:
        return None
    kind, files = got
    return {"type": "pr", "subject": subject, "kind": kind, "repo": config.GITHUB_REPO or "local-dry-run", "base": config.GITHUB_BASE,
            "branch": f"fl/{subject}", "files": {path: _sha(text) for path, text in sorted(files.items())}}


def propose_pr(st: Store, subject: str, rationale: str = "") -> dict:
    """Propose a draft pull request carrying a VERIFIED fix (compose override + gate report), an approved guardrail
    (policy YAML + note) or an experiment's incident report. `subject` is the fix id, guardrail action id or experiment id."""
    try:
        p = _pr_payload(st, subject)
        if not p:
            return {"ok": False, "error": f"{subject} is not a VERIFIED fix, an approved guardrail action or an experiment with an incident window"}
        return _store(st, p, f"Draft PR: {p['kind']} {subject} ({len(p['files'])} files)", rationale or "every approved change leaves the graph as a reviewable PR")
    except guard.ReadOnlyError as exc:
        return {"ok": False, "error": str(exc)}


def _gh(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)


def _execute_issue(st: Store, payload: dict) -> dict:
    inc = analysis.incident(st, payload["window"])
    title, body = incident_report(inc)
    if _sha(body) != payload["body_sha"]:
        raise RuntimeError("the incident body changed since approval")
    if config.GITHUB_REPO and shutil.which("gh"):
        proc = _gh("issue", "create", "--repo", config.GITHUB_REPO, "--title", title, "--body", body)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip()[-300:])
        return {"filed": proc.stdout.strip(), "title": title}
    (_out() / "issues").mkdir(parents=True, exist_ok=True)
    path = _out() / "issues" / f"{payload['window']}.md"
    path.write_text(f"# {title}\n\n{body}")
    return {"written": str(path.relative_to(config.ROOT)), "title": title, "note": "GITHUB_REPO not set: issue written locally instead of filed"}


def _execute_pr(st: Store, payload: dict) -> dict:
    got = pr_files(st, payload["subject"])
    if not got:
        raise RuntimeError("the change no longer exists")
    kind, files = got
    if {p: _sha(t) for p, t in files.items()} != payload["files"]:
        raise RuntimeError("the files changed since approval")
    body = f"Faultline: {kind} `{payload['subject']}`\n\n" + "\n\n".join(t for p, t in sorted(files.items()) if p.endswith(".md")) + "\n\nEvery file in this PR was generated from the trace graph and approved by a person."
    if not (config.GITHUB_REPO and shutil.which("gh")):
        folder = _out() / "pr" / payload["subject"]
        for path, text in files.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_text(text)
        (folder / "PR_BODY.md").write_text(body)
        return {"written": str(folder.relative_to(config.ROOT)), "files": sorted(files), "note": "GITHUB_REPO not set: PR files written locally instead of opened"}
    repo, base, branch = config.GITHUB_REPO, payload["base"], payload["branch"]
    sha = _gh("api", f"repos/{repo}/git/ref/heads/{base}", "--jq", ".object.sha")
    if sha.returncode:
        raise RuntimeError(f"cannot read {base}: {sha.stderr.strip()[-200:]}")
    ref = _gh("api", "-X", "POST", f"repos/{repo}/git/refs", "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha.stdout.strip()}")
    if ref.returncode and "Reference already exists" not in ref.stderr:
        raise RuntimeError(f"cannot create branch {branch}: {ref.stderr.strip()[-200:]}")
    for path, text in sorted(files.items()):
        cur = _gh("api", f"repos/{repo}/contents/{path}?ref={branch}")
        args = ["api", "-X", "PUT", f"repos/{repo}/contents/{path}", "-f", f"message=Faultline: {kind} {payload['subject']}",
                "-f", f"content={base64.b64encode(text.encode()).decode()}", "-f", f"branch={branch}"]
        if cur.returncode == 0:
            args += ["-f", f"sha={json.loads(cur.stdout)['sha']}"]
        put = _gh(*args)
        if put.returncode:
            raise RuntimeError(f"cannot commit {path}: {put.stderr.strip()[-200:]}")
    pr = _gh("pr", "create", "--repo", repo, "--draft", "--base", base, "--head", branch, "--title", f"Faultline: {kind} {payload['subject']}", "--body-file", "-", stdin=body)
    if pr.returncode:
        raise RuntimeError(f"cannot open PR: {pr.stderr.strip()[-200:]}")
    return {"opened": pr.stdout.strip(), "files": sorted(files)}
