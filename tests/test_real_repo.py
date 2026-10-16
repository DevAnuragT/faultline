"""A real open-source Flask app (microblog v0.15) through the identical pipeline.

Slow on a cold cache (downloads ~15 dependency sources, maps advisories with Gemini), so it is opt-in:
    RUN_REAL_REPO=1 make test
"""
import os

import pytest

from reachable import analysis, ingest, planner, targets, verify
from reachable.graph import Store

try:
    from falkordb import FalkorDB
    FalkorDB().list_graphs()
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.skipif(not DB_UP, reason="FalkorDB not running on localhost:6379"),
              pytest.mark.skipif(not os.environ.get("RUN_REAL_REPO"), reason="set RUN_REAL_REPO=1 (slow, needs network)")]


@pytest.fixture(scope="module")
def st():
    t = targets.get("microblog")
    store = Store("reachable_microblog_test")
    ingest.full_ingest(store, progress=lambda *_: None, target=t)
    analysis.triage(store)
    yield store
    store.reset()


def verdicts(st):
    return {f["vuln"]: f for f in analysis.latest_findings(st)}


def test_real_repo_is_a_real_flask_app(st):
    assert targets.of_store(st).name == "microblog"
    routes = {r[0] for r in st.q("MATCH (e:Endpoint) RETURN e.id")}
    assert {"POST /translate", "GET /login", "POST /reset_password/<token>"} <= routes
    assert st.q("MATCH (f:Function) RETURN count(f)")[0][0] > 5000


def test_pyjwt_decode_is_reachable_from_the_reset_password_endpoint(st):
    f = verdicts(st)["GHSA-42vr-xj54-vc7v"]
    assert f["verdict"] == "REACHABLE"
    path = [p["function"] for p in analysis.witness_path(st, f["finding"])]
    assert path[0] == "app.auth.routes.reset_password"
    assert "app.models.User.verify_reset_password_token" in path and path[-1].endswith("_decode_payload")


def test_requests_advisory_reaches_through_the_translate_endpoint(st):
    f = verdicts(st)["GHSA-9hjg-9r4m-mvj7"]
    assert f["verdict"] == "REACHABLE"
    assert analysis.witness_path(st, f["finding"])[0]["function"] == "app.main.routes.translate_text"


def test_closed_loop_on_the_real_repo_is_verified_with_coverage_retained(st):
    res = verify.run_closed_loop(st, progress=lambda *_: None)
    assert res["status"] == "VERIFIED", res["gates"]
    assert all(c["after"] > 0 for c in res["coverage"].values())
    assert res["remaining_exposed"] == [] and len(res["cleared"]) >= 8
    assert "targets/microblog/requirements.txt" in res["requirements_diff"]
    assert planner.consistency_conflicts(res["plan"]["pins_after"]) == []
