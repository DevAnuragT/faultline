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
    for res in (box.propose_issue("GHSA-q2q7-5pp4-w6pg"), box.propose_vex(), box.propose_fix_pr()):
        assert res["ok"] is False and "read-only public demo" in res["error"]
    assert not st.q("MATCH (a:Action) RETURN a")  # nothing was written


def _graph_fingerprint(st):
    return (st.q("MATCH (n) RETURN count(n)")[0][0], st.q("MATCH ()-[r]->() RETURN count(r)")[0][0],
            st.q("MATCH (m:Remediation) RETURN count(m)")[0][0], st.q("MATCH (r:Run) RETURN count(r)")[0][0])


def test_public_visitors_can_verify_live_without_writing_anything(st, public, monkeypatch):
    """The closed loop runs for real in a private scratch graph, but the shared graph is untouched afterwards."""
    monkeypatch.setattr(verify, "_public_last_finished", 0.0)
    monkeypatch.setattr(config, "PUBLIC_MODE", False)
    verify.run_closed_loop(st, progress=lambda *_: None)  # the operator's recorded run (what `make seed` leaves behind)
    monkeypatch.setattr(config, "PUBLIC_MODE", True)
    before = _graph_fingerprint(st)
    graphs_before = set(st.db.list_graphs())
    res = verify.run_closed_loop_ephemeral(st, progress=lambda *_: None)
    assert res["status"] == "VERIFIED" and res["persisted"] is False and res["cleared"]
    assert _graph_fingerprint(st) == before
    assert set(st.db.list_graphs()) == graphs_before  # the scratch graph was deleted
    # one at a time, with a cooldown: a second visitor right away is told to wait instead of queueing work
    with pytest.raises(verify.BusyError):
        verify.run_closed_loop_ephemeral(st, progress=lambda *_: None)
    # the agent tool reports the live result, or the recorded one while the cooldown lasts, never an error
    out = Toolbox(st).verify_remediation()
    assert out.get("ok", True) and out["status"] in ("VERIFIED", "REJECTED", "UNVERIFIED") and out["note"]
    assert _graph_fingerprint(st) == before
