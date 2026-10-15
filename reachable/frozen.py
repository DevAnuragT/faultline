"""Frozen, precomputed demo artifacts for the read-only public deployment.

The operator (you, locally or on the seeded cloud graph) runs the interactive what-if once; the public site shows that
recorded result instead of letting visitors mutate the shared graph.
"""
from __future__ import annotations

import json
import time

from . import analysis, ingest
from .config import ROOT
from .graph import Store

FROZEN = ROOT / "data" / "frozen"
WHATIF_FILE = FROZEN / "whatif.json"


def record_whatif(st: Store, variant: str = "no_preview_fetch") -> dict:
    """Run the what-if for real, record the verdict flips with snapshot ids, then restore the baseline graph."""
    base_findings = analysis.latest_findings(st)
    base_run = analysis.triage(st, label="baseline")
    before = {f["vuln"]: f for f in analysis.latest_findings(st, base_run)}
    ingest.reload_app_layer(st, variant)
    alt_run = analysis.triage(st, label=variant)
    after = {f["vuln"]: f for f in analysis.latest_findings(st, alt_run)}
    flips = [{"vuln": v, "package": before[v]["package"], "version": before[v]["version"], "before": before[v]["verdict"],
              "after": after[v]["verdict"]} for v in before if before[v]["verdict"] != after[v]["verdict"]]
    result = {
        "variant": variant, "description": ingest.VARIANTS[variant]["label"], "recorded_at": int(time.time()),
        "baseline_snapshot": analysis.run_snapshot(st, base_run), "variant_snapshot": analysis.run_snapshot(st, alt_run),
        "flips": sorted(flips, key=lambda f: (f["before"], f["vuln"])),
        "exposed_before": sum(1 for f in before.values() if f["verdict"] in ("REACHABLE", "POSSIBLY_REACHABLE")),
        "exposed_after": sum(1 for f in after.values() if f["verdict"] in ("REACHABLE", "POSSIBLY_REACHABLE")),
        "previous_runs_seen": len(base_findings),
    }
    ingest.reload_app_layer(st, "baseline")
    analysis.triage(st, label="baseline")
    FROZEN.mkdir(parents=True, exist_ok=True)
    WHATIF_FILE.write_text(json.dumps(result, indent=2))
    return result


def load_whatif() -> dict | None:
    return json.loads(WHATIF_FILE.read_text()) if WHATIF_FILE.exists() else None
