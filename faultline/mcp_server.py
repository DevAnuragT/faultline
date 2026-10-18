"""MCP server: the same graph tools for any MCP client (Claude Desktop, Copilot CLI, Gemini CLI).
Read and propose only; execution stays behind human approval in the UI or CLI.   Run (stdio): python -m faultline.mcp_server"""
from __future__ import annotations

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .agent import Toolbox
from .graph import Store

mcp = _Server("faultline")
_box: Toolbox | None = None


def box() -> Toolbox:
    global _box
    if _box is None:
        _box = Toolbox(Store())
    return _box


@mcp.tool()
def overview(window: str = "") -> dict:
    """Steady-state picture of a window: endpoint health, critical-path owners, service graph, findings."""
    return box().overview(window)


@mcp.tool()
def explain_critical_path(root_op: str = "", window: str = "") -> dict:
    """CRISP critical path of the slowest steady-state request plus bottom-up owners."""
    return box().explain_critical_path(root_op, window)


@mcp.tool()
def blast_radius(service: str, window: str = "") -> dict:
    """What the graph predicts breaks if `service` stops (the hypothesis of a pause experiment)."""
    return box().blast_radius(service, window)


@mcp.tool()
def rank_experiments(window: str = "") -> list[dict]:
    """Monocle-style ranking of fault experiments with ledger memory."""
    return box().rank_experiments(window)


@mcp.tool()
def remedies(window: str = "") -> list[dict]:
    """Remedy classes the graph derives from evidence (contention, serialized fan-out, per-call cost) with actuators."""
    return box().remedies(window)


@mcp.tool()
def next_fix(window: str = "") -> dict:
    """The knob that targets the top critical-path owner, with previous attempts."""
    return box().next_fix(window)


@mcp.tool()
def guardrails(window: str = "") -> list[dict]:
    """Timeouts, retry budgets and fan-out caps derived from measured latency and experiment evidence."""
    return box().guardrails(window)


@mcp.tool()
def retries(window: str = "") -> list[dict]:
    """Silent retries found in span trees."""
    return box().retries(window)


@mcp.tool()
def attack_surface(window: str = "") -> list[dict]:
    """Data stores reachable from the edge, protocol per hop, calls per request into each store."""
    return box().attack_surface(window)


@mcp.tool()
def history() -> dict:
    """Experiments, fixes and actions recorded in the graph."""
    return box().history()


@mcp.tool()
def compare_windows(window_a: str, window_b: str) -> dict:
    """Drift between two windows."""
    return box().compare_windows(window_a, window_b)


@mcp.tool()
def propose_experiment(service: str, rationale: str = "") -> dict:
    """Propose (not run) pausing a service; a human approves in the UI/CLI."""
    return box().propose_experiment(service, rationale)


@mcp.tool()
def propose_fix(flags: str, rationale: str = "") -> dict:
    """Propose (not apply) an actuator from the profile (id from next_fix); a human approves in the UI/CLI."""
    return box().propose_fix(flags, rationale)


@mcp.tool()
def propose_guardrail(hop: str, rationale: str = "") -> dict:
    """Propose a timeout + bounded-retry policy for one hop (e.g. "frontend -> route"); a human approves."""
    return box().propose_guardrail(hop, rationale)


@mcp.tool()
def incidents() -> list[dict]:
    """Windows that regressed against steady state, with the owner that moved, hop, code pointer and worst trace."""
    return box().incidents()


@mcp.tool()
def code_pointer(service: str, op: str) -> dict:
    """Where in the source an operation lives (span code attributes, else the demo source map)."""
    return box().code_pointer(service, op)


@mcp.tool()
def read_source(service: str, op: str) -> dict:
    """The function behind an operation: file, lines, code, url."""
    return box().read_source(service, op)


@mcp.tool()
def propose_code_change(service: str, op: str, patch: str, summary: str) -> dict:
    """Propose a unified-diff change to a function read with read_source; a human approves; becomes a draft PR."""
    return box().propose_code_change(service, op, patch, summary)


@mcp.tool()
def propose_incident_issue(window: str, rationale: str = "") -> dict:
    """Propose an incident issue for a regressed window; a human approves before anything is filed."""
    return box().propose_incident_issue(window, rationale)


@mcp.tool()
def propose_pr(subject: str, rationale: str = "") -> dict:
    """Propose a draft PR for a VERIFIED fix, an approved guardrail or an experiment's incident report; a human approves."""
    return box().propose_pr(subject, rationale)


@mcp.tool()
def query_graph(cypher: str) -> dict:
    """Read-only Cypher against the graph (max 25 rows)."""
    return box().query_graph(cypher)


if __name__ == "__main__":
    mcp.run()
