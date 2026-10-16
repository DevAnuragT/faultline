"""Actions the agent can *propose*. A human approves; deterministic code re-derives the payload and executes.

Authorization is bound to evidence, not to a verdict label. Every action stores a canonical payload (target, content
hashes, graph snapshot, witness path / pins) and its SHA-256. On approval we
  1. check the stored payload and content still hash to the stored value (tamper check),
  2. rebuild the payload from the *current* graph and compare (stale check: any change to the evidence expires it),
  3. only then execute. One executed action per (type, subject); re-approving is a no-op.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import time
import uuid

from . import analysis, config, guard, targets, verify
from .graph import Store

OUT = config.ROOT / "out"
EXPOSED = {"REACHABLE", "POSSIBLY_REACHABLE"}
BASE_BRANCH = "master"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict) -> str:
    return _sha(canonical(payload))


def _repo() -> str:
    return config.GITHUB_REPO or "local-dry-run"


def _current_finding(st: Store, vuln_id: str) -> dict | None:
    return next((f for f in analysis.latest_findings(st) if f["vuln"] == vuln_id), None)


def _latest_snapshot(st: Store) -> str:
    return analysis.run_snapshot(st, analysis.latest_findings(st)[0]["run"])


# ---------------------------------------------------------------------------------------------- issue
def _issue_content(st: Store, f: dict) -> tuple[str, str, str | None, list[str]]:
    path = analysis.witness_path(st, f["finding"])
    app_fns = [p["function"] for p in path if p["layer"] == "app" and not p["function"].endswith(".<module>")]
    guard_fn = app_fns[-1] if app_fns else None
    team = analysis.owner_of(st, guard_fn) if guard_fn else None
    title = f"[{f['verdict']}] {f['package']} {f['version']}: {f['cve'] or f['vuln']}"
    chain = "\n".join(f"{p['step'] + 1}. `{p['function']}`" + (f"  _(via {p['via']})_" if p["via"] != "ENTRY" else "  _(entry)_")
                      for p in path)
    fix = f"Upgrade `{f['package']}` to `>={f['fixed_version']}`." if f["fixed_version"] else "No fixed version known."
    body = (f"**{f['summary']}**\n\nAdvisory: {f['vuln']} ({f['cve'] or 'no CVE'}), severity {f['severity']}\n\n"
            f"**Why this is exposed:** {f['reason']}\n\n**Call path from the public endpoint**\n{chain}\n\n"
            f"**Suggested fix:** {fix}\n"
            + (f"**Guard point in our code:** `{guard_fn}`\n" if guard_fn else "")
            + (f"**Owner (from CODEOWNERS):** {team}\n" if team else "")
            + f"\n_Vulnerable symbol identified by {f['symbol_source']} (confidence {f['symbol_confidence']}): "
              f"{f['symbol_rationale']}_\n_Static analysis only; verify before closing._")
    return title, body, team, [f"{p['function']}|{p['via']}" for p in path]


def _issue_payload(st: Store, vuln_id: str) -> tuple[dict | None, dict | None]:
    f = _current_finding(st, vuln_id)
    if not f or f["verdict"] not in EXPOSED:
        return None, f
    title, body, team, witness = _issue_content(st, f)
    return ({"type": "issue", "repo": _repo(), "vuln": vuln_id, "verdict": f["verdict"], "title": title,
             "body_sha": _sha(body), "team": team or "", "witness": witness,
             "snapshot": _latest_snapshot(st)}, f)


# ---------------------------------------------------------------------------------------------- vex
def build_vex(st: Store) -> dict:
    status = {
        "REACHABLE": "affected",
        "POSSIBLY_REACHABLE": "under_investigation",
        "NEEDS_REVIEW": "under_investigation",
        # Never assert not_affected from a static scan: dynamic dispatch is not modelled.
        "NO_STATIC_PATH": "under_investigation",
    }
    statements = []
    for f in analysis.latest_findings(st):
        stmt = {"vulnerability": {"name": f["cve"] or f["vuln"], "@id": f["vuln"]},
                "products": [{"@id": f"pkg:pypi/{f['package'].lower()}@{f['version']}"}], "status": status[f["verdict"]]}
        if f["verdict"] == "NO_STATIC_PATH":
            stmt["status_notes"] = ("Reachable found no static call path from any public endpoint to the vulnerable "
                                    "function in this application revision. Dynamic dispatch is not modelled, so this "
                                    "is not an assertion of non-exposure.")
        elif stmt["status"] == "affected":
            stmt["action_statement"] = f["fixed_version"] and f"Upgrade to >={f['fixed_version']}" or "See advisory"
        statements.append(stmt)
    return {"@context": "https://openvex.dev/ns/v0.2.0", "@id": f"https://reachable.local/vex/{int(time.time())}",
            "author": "Reachable (automated static analysis)",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "version": 1, "statements": statements}


def _vex_payload(st: Store) -> dict:
    return {"type": "vex", "snapshot": _latest_snapshot(st),
            "statements_sha": _sha(canonical(build_vex(st)["statements"]))}


# ---------------------------------------------------------------------------------------------- pr
def _pr_payload(rem: dict, st: Store) -> dict:
    return {"type": "pr", "repo": _repo(), "base": BASE_BRANCH, "branch": f"reachable/fix-{rem['id']}",
            "path": targets.of_store(st).repo_path,
            "remediation": rem["id"], "status": rem["status"], "diff_sha": _sha(rem["requirements_diff"]),
            "pins_after": rem["plan"]["pins_after"], "baseline_snapshot": rem["baseline_snapshot"],
            "candidate_snapshot": rem["candidate_snapshot"]}


def _remediation(st: Store, rem_id: str) -> dict | None:
    rows = st.q("MATCH (m:Remediation {id:$id}) RETURN m.result", {"id": rem_id})
    return json.loads(rows[0][0]) if rows else None


# ---------------------------------------------------------------------------------------------- propose
def _store_action(st: Store, typ: str, subject: str, title: str, payload: dict, body: str = "", team: str = "",
                  link: tuple[str, str] | None = None) -> dict:
    existing = st.q("MATCH (a:Action {type:$t, subject:$s}) WHERE a.status IN ['proposed','done','dry_run'] "
                    "RETURN a.id, a.status", {"t": typ, "s": subject})
    if existing:
        return {"ok": True, "action": existing[0][0], "status": existing[0][1], "note": "already exists"}
    aid = f"act-{uuid.uuid4().hex[:8]}"
    st.q("CREATE (a:Action {id:$id, type:$t, subject:$s, status:'proposed', title:$title, body:$body, team:$team, "
         "payload:$payload, hash:$hash, ts:$ts})",
         {"id": aid, "t": typ, "s": subject, "title": title, "body": body, "team": team,
          "payload": canonical(payload), "hash": payload_hash(payload), "ts": int(time.time())})
    if link:
        st.q(f"MATCH (a:Action {{id:$id}}), (n:{link[0]} {{id:$nid}}) CREATE (a)-[:RESOLVES]->(n)",
             {"id": aid, "nid": link[1]})
    if team:
        st.q("MATCH (a:Action {id:$id}), (t:Team {name:$t}) CREATE (a)-[:ASSIGNED_TO]->(t)", {"id": aid, "t": team})
    return {"ok": True, "action": aid, "status": "proposed", "hash": payload_hash(payload)[:12]}


def propose_issue(st: Store, vuln_id: str) -> dict:
    guard.require_writable("Proposing actions")
    payload, f = _issue_payload(st, vuln_id)
    if payload is None:
        if not f:
            return {"ok": False, "error": f"unknown vulnerability {vuln_id}"}
        return {"ok": False, "error": f"{vuln_id} is {f['verdict']}; issues are only filed for exposed findings"}
    title, body, team, _ = _issue_content(st, f)
    res = _store_action(st, "issue", vuln_id, title, payload, body, team or "", ("Finding", f["finding"]))
    res.update(title=title, assignee_team=team)
    return res


def propose_vex(st: Store) -> dict:
    guard.require_writable("Proposing actions")
    if not analysis.latest_findings(st):
        return {"ok": False, "error": "no triage run yet"}
    payload = _vex_payload(st)
    return _store_action(st, "vex", payload["snapshot"], "OpenVEX statements for latest triage run", payload)


def propose_fix_pr(st: Store, remediation_id: str | None = None) -> dict:
    guard.require_writable("Proposing actions")
    rem = _remediation(st, remediation_id) if remediation_id else verify.latest_remediation(st)
    if not rem:
        return {"ok": False, "error": "no remediation has been checked yet; run verify_remediation first"}
    if rem["status"] != "VERIFIED":
        return {"ok": False, "error": f"remediation {rem['id']} is {rem['status']}; a PR is only proposed for VERIFIED fixes"}
    if rem["baseline_snapshot"] != _latest_snapshot(st):
        return {"ok": False, "error": "remediation was checked against an older graph snapshot; re-run verification"}
    changes = ", ".join(f"{c['package']} {c['from']}->{c['to']}" for c in rem["plan"]["changes"])
    res = _store_action(st, "pr", rem["id"], f"Draft PR: {changes}", _pr_payload(rem, st), link=("Remediation", rem["id"]))
    res["title"] = f"Draft PR: {changes}"
    return res


# ---------------------------------------------------------------------------------------------- execute
def _fresh_payload(st: Store, typ: str, subject: str) -> dict | None:
    if typ == "issue":
        return _issue_payload(st, subject)[0]
    if typ == "vex":
        return _vex_payload(st)
    if typ == "pr":
        rem = _remediation(st, subject)
        if not rem or rem["status"] != "VERIFIED" or rem["baseline_snapshot"] != _latest_snapshot(st):
            return None
        return _pr_payload(rem, st)
    return None


def _diff_keys(a: dict, b: dict | None) -> list[str]:
    if b is None:
        return ["evidence no longer supports this action"]
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def execute(st: Store, action_id: str) -> dict:
    """Run an approved action. Called by the human-approval path only, never by the model."""
    guard.require_writable("Executing actions")
    rows = st.q("MATCH (a:Action {id:$id}) RETURN a.type, a.subject, a.status, a.title, a.body, a.payload, a.hash",
                {"id": action_id})
    if not rows:
        return {"ok": False, "error": "unknown action"}
    typ, subject, status, title, body, payload_json, stored_hash = rows[0]
    if status in ("done", "dry_run"):
        return {"ok": True, "status": status, "note": "already executed (idempotent)"}
    stored = json.loads(payload_json)

    def refuse(kind: str, why: str) -> dict:
        st.q("MATCH (a:Action {id:$id}) SET a.status=$s", {"id": action_id, "s": kind})
        return {"ok": False, "error": why}

    if payload_hash(stored) != stored_hash:
        return refuse("tampered", "tampered: the stored payload no longer matches its approval hash; nothing executed")
    if typ == "issue" and _sha(body) != stored["body_sha"]:
        return refuse("tampered", "tampered: the issue body changed after approval; nothing executed")
    if typ == "pr":
        rem = _remediation(st, subject)
        if rem is None or _sha(rem["requirements_diff"]) != stored["diff_sha"]:
            return refuse("tampered", "tampered: the requirements diff changed after approval; nothing executed")
    fresh = _fresh_payload(st, typ, subject)
    if fresh is None or payload_hash(fresh) != stored_hash:
        return refuse("stale", "stale plan: the evidence changed since this action was proposed (changed: "
                      + ", ".join(_diff_keys(stored, fresh)) + "); nothing executed")

    OUT.mkdir(exist_ok=True)
    if typ == "vex":
        path = OUT / "vex.openvex.json"
        path.write_text(json.dumps(build_vex(st), indent=2))
        st.q("MATCH (a:Action {id:$id}) SET a.status='done', a.url=$u", {"id": action_id, "u": str(path)})
        return {"ok": True, "status": "done", "file": str(path)}
    if typ == "pr":
        return _execute_pr(st, action_id, _remediation(st, subject), stored)
    return _execute_issue(st, action_id, subject, title, body)


def _execute_issue(st: Store, action_id: str, vuln: str, title: str, body: str) -> dict:
    if config.GITHUB_REPO and shutil.which("gh"):
        proc = subprocess.run(["gh", "issue", "create", "--repo", config.GITHUB_REPO, "--title", title, "--body", body],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            url = proc.stdout.strip()
            st.q("MATCH (a:Action {id:$id}) SET a.status='done', a.url=$u", {"id": action_id, "u": url})
            return {"ok": True, "status": "done", "url": url}
        return {"ok": False, "error": proc.stderr.strip()[:300]}
    (OUT / "issues").mkdir(parents=True, exist_ok=True)
    path = OUT / "issues" / f"{vuln}.md"
    path.write_text(f"# {title}\n\n{body}\n")
    st.q("MATCH (a:Action {id:$id}) SET a.status='dry_run', a.url=$u", {"id": action_id, "u": str(path)})
    return {"ok": True, "status": "dry_run", "file": str(path),
            "note": "GITHUB_REPO not set: issue written locally instead of filed"}


def _gh(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)


def _execute_pr(st: Store, action_id: str, rem: dict, stored: dict) -> dict:
    report = verify.render_report(rem)
    if not (config.GITHUB_REPO and shutil.which("gh")):
        folder = OUT / "remediation" / rem["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "requirements.diff").write_text(rem["requirements_diff"])
        (folder / "report.md").write_text(report)
        st.q("MATCH (a:Action {id:$id}) SET a.status='dry_run', a.url=$u", {"id": action_id, "u": str(folder)})
        return {"ok": True, "status": "dry_run", "file": str(folder),
                "note": "GITHUB_REPO not set: PR diff and report written locally"}
    repo, base, branch, path = config.GITHUB_REPO, stored["base"], stored["branch"], stored["path"]
    sha = _gh("api", f"repos/{repo}/git/ref/heads/{base}", "--jq", ".object.sha")
    if sha.returncode:
        return {"ok": False, "error": f"cannot read {base}: {sha.stderr.strip()[:200]}"}
    ref = _gh("api", "-X", "POST", f"repos/{repo}/git/refs", "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha.stdout.strip()}")
    if ref.returncode:
        return {"ok": False, "error": f"cannot create branch {branch}: {ref.stderr.strip()[:200]}"}
    cur = _gh("api", f"repos/{repo}/contents/{path}?ref={base}")
    if cur.returncode:
        return {"ok": False, "error": f"cannot read {path}: {cur.stderr.strip()[:200]}"}
    meta = json.loads(cur.stdout)
    text = base64.b64decode(meta["content"]).decode()
    for pkg, ver in stored["pins_after"].items():
        text = re.sub(rf"(?m)^({re.escape(pkg)})==[\w.]+$", rf"\g<1>=={ver}", text)
    put = _gh("api", "-X", "PUT", f"repos/{repo}/contents/{path}", "-f", f"message=Upgrade vulnerable dependencies ({rem['id']})",
              "-f", f"content={base64.b64encode(text.encode()).decode()}", "-f", f"sha={meta['sha']}", "-f", f"branch={branch}")
    if put.returncode:
        return {"ok": False, "error": f"cannot commit: {put.stderr.strip()[:200]}"}
    pr = _gh("pr", "create", "--repo", repo, "--draft", "--base", base, "--head", branch,
             "--title", "Upgrade vulnerable dependencies (graph-checked)", "--body-file", "-", stdin=report)
    if pr.returncode:
        return {"ok": False, "error": f"cannot open PR: {pr.stderr.strip()[:200]}"}
    url = pr.stdout.strip()
    st.q("MATCH (a:Action {id:$id}) SET a.status='done', a.url=$u", {"id": action_id, "u": url})
    return {"ok": True, "status": "done", "url": url}


def list_actions(st: Store) -> list[dict]:
    rows = st.q("MATCH (a:Action) RETURN a.id, a.type, a.subject, a.status, a.title, a.url, a.team ORDER BY a.ts DESC")
    return [dict(id=r[0], type=r[1], vuln=r[2], status=r[3], title=r[4], url=r[5], team=r[6]) for r in rows]
