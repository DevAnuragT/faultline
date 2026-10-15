"""Evidence-bound approval tests (need local FalkorDB)."""
import json

import pytest

from reachable import actions, analysis, ingest, verify
from reachable.graph import Store

try:
    from falkordb import FalkorDB
    FalkorDB().list_graphs()
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = pytest.mark.skipif(not DB_UP, reason="FalkorDB not running on localhost:6379")
EXPOSED_VULN = "GHSA-q2q7-5pp4-w6pg"


@pytest.fixture()
def st():
    store = Store("reachable_actions_test")
    ingest.full_ingest(store, progress=lambda *_: None)
    analysis.triage(store)
    yield store
    store.reset()


def test_issue_approval_survives_a_retriage_of_the_same_graph(st):
    proposal = actions.propose_issue(st, EXPOSED_VULN)
    analysis.triage(st, label="again")  # new run, identical evidence: still valid
    assert actions.execute(st, proposal["action"])["ok"]


def test_same_verdict_but_changed_witness_path_expires_the_approval(st):
    proposal = actions.propose_issue(st, EXPOSED_VULN)
    # Change the evidence while the verdict stays REACHABLE: add a shortcut so the witness path changes.
    st.q("MATCH (a:Function {qualname:'vulnshop.preview.fetch_preview'}), (b:Function {qualname:'urllib3.util.url.parse_url'}) "
         "CREATE (a)-[:CALLS]->(b)")
    analysis.triage(st, label="shortcut")
    now = next(f for f in analysis.latest_findings(st) if f["vuln"] == EXPOSED_VULN)
    assert now["verdict"] == "REACHABLE"
    res = actions.execute(st, proposal["action"])
    assert not res["ok"] and "stale" in res["error"] and "witness" in res["error"]


def test_tampering_with_the_issue_body_after_proposal_aborts(st):
    proposal = actions.propose_issue(st, EXPOSED_VULN)
    st.q("MATCH (a:Action {id:$id}) SET a.body = 'attacker-edited body'", {"id": proposal["action"]})
    res = actions.execute(st, proposal["action"])
    assert not res["ok"] and "tampered" in res["error"]


def test_tampering_with_the_stored_payload_aborts(st):
    proposal = actions.propose_issue(st, EXPOSED_VULN)
    row = st.q("MATCH (a:Action {id:$id}) RETURN a.payload", {"id": proposal["action"]})[0][0]
    forged = json.loads(row)
    forged["repo"] = "someone/else"
    st.q("MATCH (a:Action {id:$id}) SET a.payload=$p", {"id": proposal["action"], "p": json.dumps(forged, sort_keys=True,
                                                                                               separators=(",", ":"))})
    res = actions.execute(st, proposal["action"])
    assert not res["ok"] and "tampered" in res["error"]


def test_pr_requires_a_verified_remediation_and_aborts_if_the_diff_is_edited(st):
    assert not actions.propose_fix_pr(st)["ok"]  # nothing verified yet
    rem = verify.run_closed_loop(st, progress=lambda *_: None)
    assert rem["status"] == "VERIFIED"
    proposal = actions.propose_fix_pr(st)
    assert proposal["ok"]
    edited = {**rem, "requirements_diff": rem["requirements_diff"] + "+evil==1.0\n"}
    st.q("MATCH (m:Remediation {id:$id}) SET m.result=$r", {"id": rem["id"], "r": json.dumps(edited)})
    res = actions.execute(st, proposal["action"])
    assert not res["ok"] and "tampered" in res["error"]


def test_pr_dry_run_writes_diff_and_report_when_untampered(st, tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "OUT", tmp_path)
    rem = verify.run_closed_loop(st, progress=lambda *_: None)
    proposal = actions.propose_fix_pr(st)
    res = actions.execute(st, proposal["action"])
    assert res["ok"] and res["status"] == "dry_run"
    assert "+urllib3==2.6.3" in (tmp_path / "remediation" / rem["id"] / "requirements.diff").read_text()
    assert actions.execute(st, proposal["action"])["note"].startswith("already")


def test_pr_proposal_refused_when_remediation_is_not_verified(st):
    from reachable import planner
    before = planner.current_pins(st)
    res = verify.verify_plan(st, planner.Plan(before, dict(before)), progress=lambda *_: None)
    assert res["status"] == "REJECTED"
    out = actions.propose_fix_pr(st, res["id"])
    assert not out["ok"] and "REJECTED" in out["error"]


def test_real_pr_path_orchestration_with_a_fake_gh(st, monkeypatch):
    import base64
    import subprocess

    from reachable import config

    monkeypatch.setattr(config, "GITHUB_REPO", "o/r")  # the target repo is part of the approved payload
    verify.run_closed_loop(st, progress=lambda *_: None)
    proposal = actions.propose_fix_pr(st)
    calls = []
    original = (ingest.DEMO_APP / "requirements.txt").read_text()

    def fake_gh(*args, stdin=None):
        calls.append((args, stdin))
        out = ""
        if args[:2] == ("api", "repos/o/r/git/ref/heads/master"):
            out = "abc123\n"
        elif args[0] == "api" and "contents/demo_app/requirements.txt?ref=master" in args[1]:
            out = json.dumps({"sha": "filesha", "content": base64.b64encode(original.encode()).decode()})
        elif args[0] == "pr":
            out = "https://github.com/o/r/pull/7\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(actions.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(actions, "_gh", fake_gh)
    res = actions.execute(st, proposal["action"])
    assert res == {"ok": True, "status": "done", "url": "https://github.com/o/r/pull/7"}
    put = next(c for c in calls if "PUT" in c[0])
    committed = base64.b64decode(next(a for a in put[0] if a.startswith("content="))[8:]).decode()
    assert "urllib3==2.6.3" in committed and "requests==2.33.0" in committed and "PyYAML==5.4" in committed
    assert "Flask==2.2.2" in committed  # untouched pins preserved
    pr = next(c for c in calls if c[0][0] == "pr")
    assert "--draft" in pr[0] and "VERIFIED" in pr[1]


def test_changing_the_target_repo_after_proposal_expires_the_approval(st, monkeypatch):
    from reachable import config
    proposal = actions.propose_issue(st, EXPOSED_VULN)
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/else")
    res = actions.execute(st, proposal["action"])
    assert not res["ok"] and "repo" in res["error"]
