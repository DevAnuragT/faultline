"""Actions the agent can *propose*. A human approves; deterministic code re-checks the graph and executes.

Guards (enforced here, outside the model):
  * the finding must still hold in the latest triage run (stale plans are refused)
  * issues are only filed for REACHABLE / POSSIBLY_REACHABLE findings
  * one executed action per (type, vulnerability): re-approving is a no-op returning the first result
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from . import analysis, config
from .graph import Store

OUT = config.ROOT / "out"
EXPOSED = {"REACHABLE", "POSSIBLY_REACHABLE"}


def _current_finding(st: Store, vuln_id: str) -> dict | None:
    return next((f for f in analysis.latest_findings(st) if f["vuln"] == vuln_id), None)


def _issue_body(st: Store, f: dict) -> tuple[str, str, str | None]:
    path = analysis.witness_path(st, f["finding"])
    app_fns = [p["function"] for p in path if p["layer"] == "app" and not p["function"].endswith(".<module>")]
    guard = app_fns[-1] if app_fns else None
    team = analysis.owner_of(st, guard) if guard else None
    title = f"[{f['verdict']}] {f['package']} {f['version']}: {f['cve'] or f['vuln']}"
    chain = "\n".join(f"{p['step'] + 1}. `{p['function']}`" + (f"  _(via {p['via']})_" if p["via"] != "ENTRY" else "  _(entry)_")
                      for p in path)
    fix = f"Upgrade `{f['package']}` to `>={f['fixed_version']}`." if f["fixed_version"] else "No fixed version known."
    body = (f"**{f['summary']}**\n\nAdvisory: {f['vuln']} ({f['cve'] or 'no CVE'}), severity {f['severity']}\n\n"
            f"**Why this is exposed:** {f['reason']}\n\n**Call path from the public endpoint**\n{chain}\n\n"
            f"**Suggested fix:** {fix}\n"
            + (f"**Guard point in our code:** `{guard}`\n" if guard else "")
            + (f"**Owner:** {team}\n" if team else "")
            + f"\n_Vulnerable symbol identified by {f['symbol_source']} (confidence {f['symbol_confidence']}): "
              f"{f['symbol_rationale']}_\n_Static analysis only; verify before closing._")
    return title, body, team


def propose_issue(st: Store, vuln_id: str) -> dict:
    f = _current_finding(st, vuln_id)
    if not f:
        return {"ok": False, "error": f"unknown vulnerability {vuln_id}"}
    if f["verdict"] not in EXPOSED:
        return {"ok": False, "error": f"{vuln_id} is {f['verdict']}; issues are only filed for exposed findings"}
    existing = st.q("MATCH (a:Action {type:'issue', vuln:$v}) WHERE a.status IN ['proposed','done','dry_run'] "
                    "RETURN a.id, a.status", {"v": vuln_id})
    if existing:
        return {"ok": True, "action": existing[0][0], "status": existing[0][1], "note": "already exists"}
    title, body, team = _issue_body(st, f)
    aid = f"act-{uuid.uuid4().hex[:8]}"
    st.q("MATCH (fd:Finding {id:$fid}) CREATE (a:Action {id:$id, type:'issue', vuln:$v, status:'proposed', "
         "title:$title, body:$body, team:$team, run:$run, verdict:$verdict, ts:$ts})-[:RESOLVES]->(fd)",
         {"fid": f["finding"], "id": aid, "v": vuln_id, "title": title, "body": body, "team": team or "",
          "run": f["run"], "verdict": f["verdict"], "ts": int(time.time())})
    if team:
        st.q("MATCH (a:Action {id:$id}), (t:Team {name:$t}) CREATE (a)-[:ASSIGNED_TO]->(t)", {"id": aid, "t": team})
    return {"ok": True, "action": aid, "status": "proposed", "title": title, "assignee_team": team}


def propose_vex(st: Store) -> dict:
    findings = analysis.latest_findings(st)
    if not findings:
        return {"ok": False, "error": "no triage run yet"}
    run = findings[0]["run"]
    aid = f"act-{uuid.uuid4().hex[:8]}"
    st.q("MATCH (r:Run {id:$run}) CREATE (a:Action {id:$id, type:'vex', status:'proposed', run:$run, "
         "title:'OpenVEX statements for latest triage run', ts:$ts})-[:FOR_RUN]->(r)",
         {"run": run, "id": aid, "ts": int(time.time())})
    return {"ok": True, "action": aid, "status": "proposed", "statements": len(findings)}


def build_vex(st: Store) -> dict:
    status = {
        "REACHABLE": ("affected", None),
        "POSSIBLY_REACHABLE": ("under_investigation", None),
        "NEEDS_REVIEW": ("under_investigation", None),
        # Never assert not_affected from a static scan: dynamic dispatch is not modelled.
        "NO_STATIC_PATH": ("under_investigation", None),
    }
    statements = []
    for f in analysis.latest_findings(st):
        s, just = status[f["verdict"]]
        stmt = {"vulnerability": {"name": f["cve"] or f["vuln"], "@id": f["vuln"]},
                "products": [{"@id": f"pkg:pypi/{f['package'].lower()}@{f['version']}"}], "status": s}
        if f["verdict"] == "NO_STATIC_PATH":
            stmt["status_notes"] = ("Reachable found no static call path from any public endpoint to the vulnerable "
                                    "function in this application revision. Dynamic dispatch is not modelled, so this "
                                    "is not an assertion of non-exposure.")
        elif s == "affected":
            stmt["action_statement"] = f["fixed_version"] and f"Upgrade to >={f['fixed_version']}" or "See advisory"
        statements.append(stmt)
    return {"@context": "https://openvex.dev/ns/v0.2.0", "@id": f"https://reachable.local/vex/{int(time.time())}",
            "author": "Reachable (automated static analysis)", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": 1, "statements": statements}


def execute(st: Store, action_id: str) -> dict:
    """Run an approved action. Called by the human-approval path only, never by the model."""
    rows = st.q("MATCH (a:Action {id:$id}) RETURN a.type, a.status, a.vuln, a.title, a.body, a.team, a.verdict",
                {"id": action_id})
    if not rows:
        return {"ok": False, "error": "unknown action"}
    typ, status, vuln, title, body, team, verdict = rows[0]
    if status in ("done", "dry_run"):
        return {"ok": True, "status": status, "note": "already executed (idempotent)"}
    OUT.mkdir(exist_ok=True)
    if typ == "vex":
        path = OUT / "vex.openvex.json"
        path.write_text(json.dumps(build_vex(st), indent=2))
        st.q("MATCH (a:Action {id:$id}) SET a.status='done', a.url=$u", {"id": action_id, "u": str(path)})
        return {"ok": True, "status": "done", "file": str(path)}
    current = _current_finding(st, vuln)
    if not current or current["verdict"] != verdict or current["verdict"] not in EXPOSED:
        st.q("MATCH (a:Action {id:$id}) SET a.status='stale'", {"id": action_id})
        return {"ok": False, "error": "stale plan: the finding changed since this action was proposed; nothing executed",
                "current_verdict": current["verdict"] if current else None}
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


def list_actions(st: Store) -> list[dict]:
    rows = st.q("MATCH (a:Action) RETURN a.id, a.type, a.vuln, a.status, a.title, a.url, a.team ORDER BY a.ts DESC")
    return [dict(id=r[0], type=r[1], vuln=r[2], status=r[3], title=r[4], url=r[5], team=r[6]) for r in rows]
