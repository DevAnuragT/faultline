"""Integration test: `make demo` reproduces every number the pitch uses, from a clean ingest."""
import pytest

from reachable import demo, frozen

try:
    from falkordb import FalkorDB
    FalkorDB().list_graphs()
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = pytest.mark.skipif(not DB_UP, reason="FalkorDB not running on localhost:6379")


def test_demo_reproduces_the_pitch_numbers():
    r = demo.run(progress=lambda *_: None)
    assert r["advisories"] == 33
    assert r["verdicts"] == {"REACHABLE": 4, "POSSIBLY_REACHABLE": 7, "NEEDS_REVIEW": 18, "NO_STATIC_PATH": 4}
    assert r["exposed"] == 11
    assert r["flagship"]["verdict"] == "REACHABLE" and r["flagship"]["exact_only"] and r["flagship"]["hops"] == 8
    assert r["flagship"]["path"][0] == "vulnshop.app.link_preview"
    assert r["flagship"]["path"][-1] == "urllib3.util.url.parse_url"
    assert any("requests 2.25.1 requires urllib3<1.27" in x for x in r["plan"]["rejected"])
    assert r["remediation"]["status"] == "VERIFIED" and all(r["remediation"]["gates"].values())
    assert r["remediation"]["cleared"] == 11 and r["remediation"]["new_exposed"] == 0
    assert r["approval"]["ok"] and r["approval"]["status"] == "proposed"
    assert r["whatif"]["flips"] == 10


def test_frozen_whatif_matches_a_live_run():
    fw = frozen.load_whatif()
    assert fw and len(fw["flips"]) == 10 and fw["exposed_before"] == 11
