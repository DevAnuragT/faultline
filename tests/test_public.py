"""Read-only public mode is enforced in the backend, not just by hiding buttons."""
import pytest

from reachable import actions, analysis, config, guard, ingest, planner, verify
from reachable.agent import Agent, Toolbox
from reachable.graph import Store

try:
    from falkordb import FalkorDB
    FalkorDB().list_graphs()
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = pytest.mark.skipif(not DB_UP, reason="FalkorDB not running on localhost:6379")


@pytest.fixture(scope="module")
def st():
    store = Store("reachable_public_test")
    ingest.full_ingest(store, progress=lambda *_: None)
    analysis.triage(store)
    yield store
    store.reset()


@pytest.fixture()
def public(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_MODE", True)


def test_every_mutating_backend_function_refuses(st, public):
    before = planner.current_pins(st)
    mutators = [
        lambda: ingest.full_ingest(st, progress=lambda *_: None),
        lambda: ingest.reload_app_layer(st, "no_preview_fetch"),
        lambda: analysis.triage(st),
        lambda: actions.propose_issue(st, "GHSA-q2q7-5pp4-w6pg"),
        lambda: actions.propose_vex(st),
        lambda: actions.propose_fix_pr(st),
        lambda: actions.execute(st, "act-anything"),
        lambda: verify.verify_plan(st, planner.Plan(before, dict(before))),
    ]
    for m in mutators:
        with pytest.raises(guard.ReadOnlyError):
            m()


def test_reads_still_work_in_public_mode(st, public):
    findings = analysis.latest_findings(st)
    assert findings and analysis.witness_path(st, findings[0]["finding"]) is not None
    box = Toolbox(st)
    assert "rows" in box.query_graph("MATCH (v:Vuln) RETURN count(v)")
    assert box.explain_finding("GHSA-q2q7-5pp4-w6pg")["verdict"] == "REACHABLE"
    assert planner.plan_remediation(st).consistent


def test_agent_tools_return_clean_errors_in_public_mode(st, public):
    box = Toolbox(st)
    for res in (box.propose_issue("GHSA-q2q7-5pp4-w6pg"), box.propose_vex(), box.propose_fix_pr(), box.verify_remediation()):
        assert res["ok"] is False and "read-only public demo" in res["error"]
    assert not st.q("MATCH (a:Action) RETURN a")  # nothing was written
