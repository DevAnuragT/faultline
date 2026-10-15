"""MCP server test: a real MCP client over stdio lists tools and calls one (needs the demo graph on local FalkorDB)."""
import asyncio
import os
import sys

import pytest

from reachable import config
from reachable.graph import Store

try:
    DB_UP = Store().exists()
except Exception:
    DB_UP = False

pytestmark = pytest.mark.skipif(not DB_UP, reason="demo graph not loaded (python -m reachable.cli ingest)")


def test_mcp_client_lists_safe_tools_and_calls_one():
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "FALKORDB_HOST": "localhost", "FALKORDB_PORT": "6379", "FALKORDB_USERNAME": "",
           "FALKORDB_PASSWORD": "", "GITHUB_REPO": "", "PYTHONPATH": str(config.ROOT)}

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "reachable.mcp_server"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                result = await session.call_tool("explain_finding", {"vuln_id": "GHSA-q2q7-5pp4-w6pg"})
                return tools, result

    tools, result = asyncio.run(asyncio.wait_for(run(), 60))
    assert {"list_findings", "explain_finding", "verify_remediation", "propose_fix_pr", "query_graph"} <= tools
    assert not any("execute" in t or "approve" in t for t in tools)
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert "REACHABLE" in text and "urllib3.util.url.parse_url" in text
