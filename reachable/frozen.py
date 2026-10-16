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
AGENT_FILE = FROZEN / "agent.json"

# The conversation recorded for the Agent tab, so a visitor sees the agent working (and every tool it chose) before
# asking anything themselves. Recorded with live Gemini against the seeded graph; every turn carries its tool trace.
AGENT_PROMPTS = [
    "Summarize the risk: what is actually reachable?",
    "Why is GHSA-q2q7-5pp4-w6pg reachable? Show the path.",
    "What should we fix first? Plan it, check it, and propose the PR.",
]


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


def _compact(result) -> str:
    """One line a reader can scan: what the tool came back with."""
    if isinstance(result, dict):
        keys = ("status", "verdict", "cleared", "new_exposed", "action", "error", "note")
        bits = [f"{k}={result[k]}" for k in keys if k in result and result[k] not in (None, "", [])]
        if "plan" in result and isinstance(result["plan"], dict):
            bits.append(f"rejected={len(result['plan'].get('rejected', []))} changes={len(result['plan'].get('changes', []))}")
        if "witness_path" in result:
            bits.append(f"path={len(result['witness_path'])} steps")
        return ", ".join(bits) or f"{len(result)} fields"
    if isinstance(result, list):
        return f"{len(result)} rows"
    return str(result)[:120]


def record_agent(st: Store, prompts: list[str] | None = None) -> dict:
    """Run the example prompts through the live agent once and store the replies with their tool traces."""
    from .agent import Agent

    agent = Agent(st)
    if not agent.live:
        raise RuntimeError("GEMINI_API_KEY is required to record the agent conversation")
    turns = []
    for prompt in prompts or AGENT_PROMPTS:
        agent = Agent(st)  # each recorded question stands alone, so every turn shows the tools chosen for it
        reply, trace = agent.ask(prompt)
        if not trace:
            raise RuntimeError(f"the agent answered {prompt!r} without calling a tool; record again")
        if "Gemini unavailable" in reply:  # offline fallback text: never record it as a Gemini answer
            raise RuntimeError(f"Gemini failed on {prompt!r}; not recording the offline fallback")
        turns.append({"prompt": prompt, "reply": reply,
                      "trace": [{"tool": t["tool"], "args": t["args"], "summary": _compact(t["result"])} for t in trace]})
    findings = analysis.latest_findings(st)
    result = {"model": agent.model, "recorded_at": int(time.time()), "run": findings[0]["run"],
              "snapshot": analysis.run_snapshot(st, findings[0]["run"]), "turns": turns}
    FROZEN.mkdir(parents=True, exist_ok=True)
    AGENT_FILE.write_text(json.dumps(result, indent=2))
    return result


def load_agent() -> dict | None:
    return json.loads(AGENT_FILE.read_text()) if AGENT_FILE.exists() else None
