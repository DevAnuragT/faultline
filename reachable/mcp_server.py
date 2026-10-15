"""MCP server: lets any MCP client (Claude Desktop, Copilot CLI, Gemini CLI...) use the same graph tools as the agent.

Exposed: read tools, remediation planning/verification and *proposing* a draft PR. Deliberately NOT exposed: anything that
executes an action. Execution stays behind human approval in the UI/CLI, bound to the evidence snapshot.

Run (stdio):  python -m reachable.mcp_server
"""
from __future__ import annotations

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .agent import Toolbox
from .graph import Store

mcp = _Server("reachable")
_box: Toolbox | None = None


def box() -> Toolbox:
    global _box
    if _box is None:
        _box = Toolbox(Store())
    return _box


@mcp.tool()
def list_findings(verdict: str = "") -> list[dict]:
    """List advisory findings from the latest triage run. Optional verdict filter: REACHABLE, POSSIBLY_REACHABLE,
    NEEDS_REVIEW or NO_STATIC_PATH."""
    return box().list_findings(verdict)


@mcp.tool()
def explain_finding(vuln_id: str) -> dict:
    """Verdict, reasoning, witness call path (with edge confidence), symbol provenance and owner for one advisory."""
    return box().explain_finding(vuln_id)


@mcp.tool()
def recommend_fixes() -> dict:
    """Guard points (app functions on the most exposed paths) and a version-aware upgrade plan."""
    return box().recommend_fixes()


@mcp.tool()
def plan_remediation() -> dict:
    """Plan upgrades using dependency constraints stored in the graph; impossible bumps are rejected with the reason."""
    return box().plan_remediation()


@mcp.tool()
def verify_remediation() -> dict:
    """Rebuild the graph with the candidate versions in a private scratch graph, re-triage and apply the gates.
    Status VERIFIED / REJECTED / UNVERIFIED (coverage dropped). Disabled in read-only public mode."""
    return box().verify_remediation()


@mcp.tool()
def supply_chain_hubs() -> list[dict]:
    """Betweenness centrality over the dependency graph."""
    return box().supply_chain_hubs()


@mcp.tool()
def query_graph(cypher: str) -> dict:
    """Run a read-only Cypher query against the FalkorDB graph (max 25 rows)."""
    return box().query_graph(cypher)


@mcp.tool()
def propose_fix_pr() -> dict:
    """Propose (never open) a draft PR for the latest VERIFIED remediation. A human must approve it elsewhere."""
    return box().propose_fix_pr()


if __name__ == "__main__":
    mcp.run()
