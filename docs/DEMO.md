# Demo script (about 2:30)

Setup: local FalkorDB, `make seed`, `make run` (writable mode, `GITHUB_REPO` set to the demo repo and `gh` logged in, if you
want a real draft PR). Open the **Graph-checked fix** tab; keep FalkorDB Browser on :3000 in another tab.
Use only numbers from `make demo`.

**0:00–0:30, the hook**
"Your scanner lists 33 advisories. Which can an attacker actually reach? Reachable loads the app and the real source of
its dependencies into FalkorDB and asks the graph." Triage tab: click GHSA-q2q7-5pp4-w6pg; the path lights up from
`GET /preview` through `requests` into `urllib3.parse_url`, 8 hops, all exact calls. "11 are exposed."

**0:30–1:15, the agent catches its own mistake**
Agent tab (opens on the recorded conversation; press Clear, then the third example button so the tool calls appear live):
"What should we fix first?" The naive plan bumps urllib3 to 2.6.3. The graph rejects it: `requests 2.25.1
requires urllib3<1.27`, a constraint stored on a graph edge. It re-plans a co-upgrade.

**1:15–1:55, graph-checked fix**
Graph-checked fix tab → "Plan & verify": a scratch graph is built with the candidate versions and re-triaged. Gates:
coverage retained, constraints consistent, exposure 11 → 0, no new exposure. "If coverage had dropped, this would say
UNVERIFIED, not fixed."

**1:55–2:20, act, bound to evidence**
"Propose draft PR" → Actions tab → Approve. The draft PR opens with the gate report (show the URL). Back in the graph, change one
edge and re-approve a sibling action: refused as stale. "Authorization expires when the evidence changes."

**2:20–2:30, why a graph**
Graph & Cypher tab: the `shortestPath` query behind every verdict. "Without FalkorDB there are no verdicts, no
evidence and no way to verify the fix."

Reset between takes: `make seed` (rebuilds, re-records the what-if). The public read-only site (`REACHABLE_PUBLIC=true`)
shows the recorded run; visitors can press "Plan & verify a fix now" (live, scratch graph) but cannot change the shared graph.
