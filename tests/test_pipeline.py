"""End-to-end tests against a live FalkorDB (docker run -p 6379:6379 falkordb/falkordb).

They use a separate graph name so the demo graph is untouched.
"""
import pytest

from reachable import actions, analysis, ingest
from reachable.agent import Toolbox
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
    store = Store("reachable_test")
    ingest.full_ingest(store, progress=lambda *_: None)
    yield store
    store.reset()


def verdicts(store):
    return {f["vuln"]: f["verdict"] for f in analysis.latest_findings(store)}


def test_exact_reachability_proof(st):
    analysis.triage(st)
    v = verdicts(st)
    assert v["GHSA-q2q7-5pp4-w6pg"] == "REACHABLE"  # urllib3 parse_url via requests.get
    run = analysis.latest_findings(st)[0]["run"]
    f = next(x for x in analysis.latest_findings(st) if x["vuln"] == "GHSA-q2q7-5pp4-w6pg")
    path = [p["function"] for p in analysis.witness_path(st, f["finding"])]
    assert path[0] == "vulnshop.app.link_preview"
    assert path[-1] == "urllib3.util.url.parse_url"
    assert all(p["via"] in ("ENTRY", "CALLS") for p in analysis.witness_path(st, f["finding"]))
    assert run


def test_dynamic_dispatch_is_possible_not_certain(st):
    assert verdicts(st)["GHSA-8q59-q68h-6hv4"] == "POSSIBLY_REACHABLE"


def test_decoy_import_is_not_reachable(st):
    # Jinja2 is imported and used by /hello, but the vulnerable filter is only registered, never called.
    assert verdicts(st)["GHSA-h5c8-rqwp-cp95"] == "NEEDS_REVIEW"
    assert verdicts(st)["GHSA-gmj6-6f8f-6699"] in ("NO_STATIC_PATH", "NEEDS_REVIEW")


def test_unanalyzed_packages_never_claim_safety(st):
    v = verdicts(st)
    assert all(v[k] == "NEEDS_REVIEW" for k in v if k.startswith("GHSA-29vq"))


def test_whatif_flips_verdict_and_stale_actions_are_refused(st):
    analysis.triage(st)
    proposal = actions.propose_issue(st, "GHSA-q2q7-5pp4-w6pg")
    assert proposal["ok"]
    ingest.reload_app_layer(st, "no_preview_fetch")
    analysis.triage(st, label="no_preview_fetch")
    assert verdicts(st)["GHSA-q2q7-5pp4-w6pg"] == "NO_STATIC_PATH"
    res = actions.execute(st, proposal["action"])
    assert not res["ok"] and "stale" in res["error"]
    assert not actions.propose_issue(st, "GHSA-q2q7-5pp4-w6pg")["ok"]
    ingest.reload_app_layer(st, "baseline")
    analysis.triage(st, label="baseline")
    assert verdicts(st)["GHSA-q2q7-5pp4-w6pg"] == "REACHABLE"


def test_execute_is_idempotent(st):
    analysis.triage(st)
    st.q("MATCH (a:Action) DETACH DELETE a")
    first = actions.propose_issue(st, "GHSA-j8r2-6x86-q33q")
    r1 = actions.execute(st, first["action"])
    r2 = actions.execute(st, first["action"])
    assert r1["ok"] and r2["ok"] and r2["note"].startswith("already")


def test_query_tool_is_read_only(st):
    box = Toolbox(st)
    assert "error" in box.query_graph("MATCH (n) DETACH DELETE n")
    assert "rows" in box.query_graph("MATCH (v:Vuln) RETURN count(v)")
    assert st.q("MATCH (v:Vuln) RETURN count(v)")[0][0] > 0


def test_betweenness_finds_supply_chain_hubs(st):
    hubs = {h["node"] for h in analysis.supply_chain_chokepoints(st)}
    assert "requests==2.25.1" in hubs


def test_whatif_preserves_historical_witness_paths(st):
    analysis.triage(st, label="before")
    old = next(f for f in analysis.latest_findings(st) if f["vuln"] == "GHSA-q2q7-5pp4-w6pg")
    before_path = [p["function"] for p in analysis.witness_path(st, old["finding"])]
    ingest.reload_app_layer(st, "no_preview_fetch")
    analysis.triage(st, label="after")
    assert [p["function"] for p in analysis.witness_path(st, old["finding"])] == before_path
    ingest.reload_app_layer(st, "baseline")
    analysis.triage(st, label="baseline")


def test_vex_never_asserts_not_affected_from_static_scan(st):
    analysis.triage(st)
    statuses = {s["status"] for s in actions.build_vex(st)["statements"]}
    assert "not_affected" not in statuses
    assert {"affected", "under_investigation"} <= statuses


def test_planner_rejects_impossible_bump_and_proposes_consistent_co_upgrade(st):
    from reachable import planner
    analysis.triage(st)
    plan = planner.plan_remediation(st)
    naive = [r for r in plan.rejected if r["package"] == "urllib3"]
    assert naive and "requests 2.25.1 requires urllib3<1.27" in naive[0]["reason"]
    assert naive[0]["resolution"].startswith("co-upgrade requests")
    assert plan.consistent
    assert plan.pins_after["urllib3"] == "2.6.3" and plan.pins_after["requests"] != "2.25.1"
    assert planner.consistency_conflicts({**plan.pins_before, "urllib3": "2.6.3"}) != []


def test_agent_fix_recommendation_never_suggests_the_impossible_bump(st):
    from reachable.agent import Toolbox
    analysis.triage(st)
    res = Toolbox(st).recommend_fixes()
    assert res["plan"]["consistent"]
