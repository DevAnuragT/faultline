"""Closed-loop remediation tests (need local FalkorDB; the candidate graph is a private scratch graph)."""
import pytest

from reachable import analysis, planner, verify
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
    from reachable import ingest
    store = Store("reachable_verify_test")
    ingest.full_ingest(store, progress=lambda *_: None)
    analysis.triage(store)
    yield store
    store.reset()


def test_verified_fix_passes_every_gate_and_clears_exposure(st):
    res = verify.run_closed_loop(st, progress=lambda *_: None)
    assert res["status"] == "VERIFIED"
    assert res["remaining_exposed"] == [] and res["new_exposed"] == []
    assert len(res["cleared"]) >= 10
    assert all(g["passed"] for g in res["gates"])
    assert "+urllib3==2.6.3" in res["requirements_diff"]
    assert res["baseline_snapshot"] and res["candidate_snapshot"] and res["baseline_snapshot"] != res["candidate_snapshot"]
    assert verify.latest_remediation(st)["id"] == res["id"]


def test_lost_analysis_coverage_is_unverified_never_success(st, monkeypatch):
    real = verify.coverage
    calls = {"n": 0}

    def shrinking(store):
        calls["n"] += 1
        data = real(store)
        return data if calls["n"] == 1 else {k: 0 for k in data}  # the candidate graph analysed nothing

    monkeypatch.setattr(verify, "coverage", shrinking)
    res = verify.run_closed_loop(st, progress=lambda *_: None)
    assert res["status"] == "UNVERIFIED"
    assert not res["gates"][0]["passed"]


def test_noop_change_is_rejected_because_exposure_does_not_drop(st):
    before = planner.current_pins(st)
    res = verify.verify_plan(st, planner.Plan(before, dict(before)), progress=lambda *_: None)
    assert res["status"] == "REJECTED"
    assert not next(g for g in res["gates"] if g["name"] == "exposure decreases")["passed"]


def test_inconsistent_pins_are_rejected(st):
    before = planner.current_pins(st)
    bad = {**before, "urllib3": "2.6.3"}  # requests 2.25.1 forbids urllib3 2.x
    plan = planner.Plan(before, bad, conflicts=planner.consistency_conflicts(bad))
    res = verify.verify_plan(st, plan, progress=lambda *_: None)
    assert res["status"] == "REJECTED"
    assert not next(g for g in res["gates"] if g["name"] == "dependency constraints consistent")["passed"]


def test_candidate_graph_analyses_the_same_packages_even_if_they_no_longer_have_advisories(st):
    """Regression: in auto mode only packages WITH advisories were parsed, so an upgraded (clean) package vanished from the
    candidate graph and 'exposure 11 -> 0' looked like a success. Verification must pass the baseline's package set."""
    from reachable import ingest, targets
    t = targets.TARGETS["vulnshop"]
    auto = type(t)(**{**t.__dict__, "parsed": None})  # auto mode
    pins = ingest.parse_requirements(t.requirements)
    vulns = ingest.query_pinned(pins)
    base = ingest.parsed_packages(auto, pins, vulns)
    clean = {**pins, "requests": "2.33.0", "urllib3": "2.6.3", "PyYAML": "5.4"}
    after = ingest.parsed_packages(auto, clean, ingest.query_pinned(clean))
    assert {"requests", "urllib3", "PyYAML"} <= set(base) and not {"requests", "urllib3", "PyYAML"} <= set(after)
    scratch = Store("reachable_parse_test")
    try:
        ingest.full_ingest(scratch, progress=lambda *_: None, pins=clean, target=auto, parse=base)
        assert {r[0] for r in scratch.q("MATCH (p:PackageVersion {parsed:true}) RETURN p.name")} >= {"requests", "urllib3", "PyYAML"}
    finally:
        scratch.reset()
