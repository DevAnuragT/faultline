"""Recorded agent conversation for the read-only public site: real Gemini turns, every tool call kept."""
from __future__ import annotations

import json
import time

from . import config
from .graph import Store

FROZEN = config.ROOT / "data" / "frozen"
AGENT_FILE = FROZEN / "agent.json"
AGENT_PROMPTS = [("What is slow and why?", {"overview", "explain_critical_path"}),
                 ("What should we fix first? Use propose_fix to propose it.", {"propose_fix", "propose_experiment"}),
                 ("What would you break next to test resilience, and what did the last experiment teach us?", {"history", "rank_experiments"})]


def _compact(result) -> str:
    if isinstance(result, dict):
        bits = [f"{k}: {result[k]}" for k in ("verdict", "status", "action", "error", "note", "text") if k in result and result[k]]
        if "health" in result and result["health"]:
            h = result["health"][0]; bits.append(f"{h['op']} p50 {h['p50_ms']} ms, {h['failed']}/{h['traces']} failed")
        if "critical_owners" in result and result["critical_owners"]:
            o = result["critical_owners"][0]; bits.append(f"top owner {o['service']} {o['op']} {o['share_pct']}%")
        if "chain" in result:
            bits.append(f"{len(result['chain'])} critical spans")
        if "flags" in result:
            bits.append(f"flags {' '.join(result['flags'])} -> {result.get('targets_owner', '')}")
        if "experiments" in result:
            bits.append(f"{len(result['experiments'])} experiments, {len(result['fixes'])} fixes, {len(result['actions'])} actions")
        return "; ".join(bits) or f"{len(result)} fields"
    if isinstance(result, list):
        return f"{len(result)} rows" + (f"; first: {result[0].get('target') or result[0].get('hop') or result[0].get('service', '')}" if result and isinstance(result[0], dict) else "")
    return str(result)[:80]


def record_agent(st: Store, prompts: list[tuple[str, set[str]]] | None = None, attempts: int = 8, pause: float = 15.0) -> dict:
    """Each prompt must produce live tool calls including one of its required tools (e.g. a real proposal)."""
    from .agent import Agent

    turns = []
    model = ""
    for prompt, required in (prompts or AGENT_PROMPTS):
        for attempt in range(attempts):  # 503s are transient and a model occasionally answers without tools: retry the turn
            ag = Agent(st)  # fresh conversation per prompt: each recorded turn shows its own tool calls
            if not ag.live:
                raise RuntimeError("GEMINI_API_KEY needed to record")
            reply, trace = ag.ask(prompt)
            if trace and not reply.startswith("(Gemini unavailable") and {t["tool"] for t in trace} & required:
                break
            time.sleep(pause)
        else:
            raise RuntimeError(f"turn without live tool calls after {attempts} attempts: {prompt!r}: {reply[:120]}")
        model = ag.model
        turns.append({"prompt": prompt, "reply": reply, "trace": [{"tool": t["tool"], "args": t["args"], "summary": _compact(t["result"])} for t in trace]})
    out = {"model": model, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "turns": turns}
    FROZEN.mkdir(parents=True, exist_ok=True)
    AGENT_FILE.write_text(json.dumps(out, indent=1))
    return out


def load_agent() -> dict | None:
    return json.loads(AGENT_FILE.read_text()) if AGENT_FILE.exists() else None
