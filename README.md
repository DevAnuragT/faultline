# Reachable

**Reachable won't touch your dependencies until the graph shows the vulnerable code is reachable, and won't open a pull request until a rebuilt graph checks the fix.**

Track: **1. Agents That Act on Connected Data** · Hackathon: Graph Hacks (WeMakeDevs × FalkorDB)

![Every exposed path, stored in FalkorDB](docs/img/exposure-map.png)

*Each red diamond is an advisory; each path is a stored witness from a public endpoint into the dependency's real source. Solid grey = exact calls, dashed amber = low-confidence links.*

## What it does

1. **Graph.** Loads an app and its pinned dependencies into FalkorDB: endpoints → functions → calls (extracted with `ast` from the *real* package sources), the dependency graph with its version constraints, and OSV advisories mapped to the vulnerable function.
2. **Judge.** Each advisory's verdict is a `shortestPath` query from public endpoints to the vulnerable function, over edge types that encode confidence. The witness path is stored on the finding as proof.
3. **Graph-checked fix.** The agent plans a compatible upgrade from the constraints in the graph (rejecting impossible bumps), builds the candidate versions into a **private scratch graph**, re-triages there, and applies gates. Only a `VERIFIED` result can become a draft PR, and approval is bound to the exact evidence.

### Coverage (the honest numbers; `make demo` reproduces them)

| | Advisories | Meaning |
|---|---|---|
| Exposed | 11 (4 `REACHABLE`, 7 `POSSIBLY_REACHABLE`) | a public endpoint reaches the vulnerable function (exact calls, or with low-confidence links) |
| No static path | 4 | no path found. **Not** a proof of safety (dynamic dispatch is not modelled) |
| Undecided | 18 | 12 are in Flask/Werkzeug (*framework packages not modelled*), 3 are Jinja2 filters only referenced in a registry, 3 have no identifiable vulnerable function |

Of 33 advisories, 11 are exposed. The closed loop takes that to 0 exposed with analysis coverage retained.

### The moment that matters

The naive plan says "bump urllib3 to 2.6.3". The graph rejects it: `requests 2.25.1 requires urllib3<1.27` (a constraint stored on a `DEPENDS_ON` edge). The planner finds the co-upgrade (`requests 2.33.0`), the candidate set is built into a scratch graph and re-triaged: **11 exposed → 0, no new exposure, coverage retained** (e.g. urllib3 479 → 512 analysed functions). Only then is a draft PR proposed. If coverage had dropped, the status would be `UNVERIFIED`, never "fixed".

## Why FalkorDB is essential

* **The verdict is a path query** over a code graph joined to dependency and advisory nodes. The product has no other way to decide.
* **Trust is part of the schema.** `CALLS` / `MAYBE_CALLS` / `REFERENCES` are different relationship types, so one engine answers both "provably" and "plausibly".
* **Evidence and authorization live in the graph.** Findings keep their witness path; approvals bind to a snapshot of the graph; a rebuilt scratch graph is how a fix is checked. Change the graph and old approvals expire.
* **Version constraints are graph edges**, so the planner can reason about co-upgrades.
* Remove FalkorDB and there are no verdicts, no evidence, no scratch-graph verification and no action authorization; the app fails closed.

(`algo.betweenness` over the 7-node dependency graph is shown as an extra tool; it is not what makes the graph essential.)

## Architecture

```
demo_app/vulnshop + pinned vulnerable deps (real PyPI sources, parsed with ast, never run)
OSV advisories + fix commits ──> callgraph.py / ingest.py ──> FalkorDB (3 layers)
                                       symbols.py: fix commit -> candidate functions -> Gemini picks
analysis.py   shortestPath verdicts, EVIDENCE, guard points      planner.py    version-aware plan
verify.py     scratch graph + gates (coverage, constraints, exposure, no new exposure)
actions.py    payload-bound approvals (tamper + stale checks) -> issue / OpenVEX / draft PR
agent.py      Gemini tool-calling agent        mcp_server.py   same tools over MCP (no execute)
streamlit_app.py  UI      guard.py  read-only public mode enforced in the backend
```

Gemini does two jobs: picking the vulnerable function from a fix-commit diff plus a deterministic candidate list (structured output, cached), and running the tool-calling agent. It never decides reachability, and it cannot execute actions. Free-tier quotas are small, so calls walk a fallback chain of models; without a key an offline mode answers from the same tools.

## Graph data model

```
(Repo)-[:CONTAINS]->(Module)-[:DEFINES]->(Function)
(Repo)-[:EXPOSES]->(Endpoint {route, method, public})-[:HANDLED_BY]->(Function)
(Repo)-[:DEPENDS_ON {direct}]->(PackageVersion {name, version, parsed})
(PackageVersion)-[:DEPENDS_ON {spec}]->(PackageVersion)             # requirement specifier, e.g. "<1.27,>=1.21.1"
(PackageVersion)-[:PROVIDES]->(Module)
(Function)-[:CALLS | :MAYBE_CALLS | :REFERENCES]->(Function)         # confidence = relationship type
(Team)-[:OWNS]->(Module)                                             # from CODEOWNERS
(Vuln {id, cve, severity, fixed_version, symbol_source})-[:AFFECTS]->(PackageVersion)
(Vuln)-[:VULNERABLE_SYMBOL {source, confidence}]->(Function)
(Run {snapshot})-[:HAS_FINDING]->(Finding {verdict, path, kinds})-[:FOR]->(Vuln)
(Finding)-[:VIA]->(Endpoint);  (Finding)-[:EVIDENCE {step, via}]->(Function)
(Remediation {status, result})-[:BASED_ON]->(Run)
(Action {type, payload, hash, status})-[:RESOLVES]->(Finding | Remediation);  (Action)-[:ASSIGNED_TO]->(Team)
```

Edge types: `CALLS` = resolved through imports / class hierarchy / local type inference; `MAYBE_CALLS` = attribute call on an unresolved receiver (restricted to packages the calling module imports, never from receivers rooted in unanalyzed imports) or runtime registry dispatch; `REFERENCES` = function used as a value.

## Core Cypher (see `reachable/analysis.py`)

```cypher
// Exact proof: shortest all-CALLS path from any public endpoint to the vulnerable function
MATCH (v:Vuln {id:$id})-[:VULNERABLE_SYMBOL]->(f:Function), (e:Endpoint {public:true})-[:HANDLED_BY]->(h:Function)
WITH v, f, e, h, shortestPath((h)-[:CALLS*1..14]->(f)) AS p
WHERE p IS NOT NULL
RETURN e.id, [n IN nodes(p) | n.qualname], [r IN relationships(p) | type(r)] ORDER BY length(p) LIMIT 1

// Plausible path: allow low-confidence edges
... shortestPath((h)-[:CALLS|MAYBE_CALLS|REFERENCES*1..14]->(f)) ...

// Version constraints the planner reads
MATCH (d:PackageVersion)-[r:DEPENDS_ON]->(p:PackageVersion {name:$pkg}) RETURN d.name, d.version, r.spec

// Guard points: app functions on the most exposed witness paths
MATCH (r:Run {id:$run})-[:HAS_FINDING]->(f:Finding)-[ev:EVIDENCE]->(n:Function {layer:'app'})
WHERE f.verdict IN ['REACHABLE','POSSIBLY_REACHABLE']
RETURN n.qualname, count(DISTINCT f) AS findings, max(ev.step) AS depth ORDER BY findings DESC, depth DESC
```

## Approvals are bound to evidence

Every action stores a canonical payload (target repo/branch, content hashes, graph **snapshot** hash, witness path or
pins) and its SHA-256. On approval the code (1) verifies the stored payload and content still hash to the stored value
(tamper check), (2) rebuilds the payload from the *current* graph and compares (stale check: any change to the evidence,
target or content aborts), and (3) only then executes. Tests cover: same verdict but changed witness path, edited issue
body, forged payload, edited diff, changed target repo, and re-approval idempotency.

## Run it

```bash
docker run -d -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest        # or: docker compose up --build
make setup                                                              # venv + dependencies (Python 3.11+)
cp .env.example .env     # GEMINI_API_KEY (optional); leave FALKORDB_USERNAME/PASSWORD empty for local
make seed                # builds the graph, runs the closed loop, records the what-if
make run                 # Streamlit UI
make demo                # clean ingest in a throwaway graph; prints every number used above
make test                # the full test suite (needs the local FalkorDB)
```

CLI: `python -m reachable.cli ingest | plan | remediate | propose-pr | approve <id> | whatif <variant> | ask "<question>"`.
FalkorDB Browser: http://localhost:3000 (e.g. `MATCH p=(:Endpoint)-[:HANDLED_BY]->()-[:CALLS*1..9]->(:Function {name:'parse_url'}) RETURN p`).

**Real GitHub PR:** set `GITHUB_REPO=owner/repo` (needs `gh auth login`); on approval the code creates a branch, commits the new
pins and opens a **draft** PR whose body is the gate report. Without it, the diff and report are written to `out/`.

**Public deployment** (read-only, enforced in `reachable/guard.py`): set `REACHABLE_PUBLIC=true`. Rebuilds, what-if edits,
verification, proposals and execution all refuse; reads and the agent's explanations work; the recorded closed loop and
what-if are shown with their snapshot ids. Seed the cloud graph first with `make seed` against it.

## MCP

`pip install -r requirements-mcp.txt`, then add to your client (Claude Desktop, Copilot CLI, Gemini CLI):

```json
{"mcpServers": {"reachable": {"command": "python", "args": ["-m", "reachable.mcp_server"], "cwd": "/path/to/reachable"}}}
```

Tools: `list_findings`, `explain_finding`, `recommend_fixes`, `plan_remediation`, `verify_remediation`, `supply_chain_hubs`,
`query_graph` (read-only), `propose_fix_pr`. There is intentionally no tool that executes an action.

## Data and limits

All data is public: the demo app is written for this project, dependencies are real PyPI releases at pinned vulnerable
versions, advisories come from the public OSV API, fixes from public GitHub commits. One mapping (PyYAML CVE-2020-14343)
is human-curated in `data/curated_symbols.json` and labelled `curated` in the UI.

* Python static call graphs are approximate; unresolved dynamic dispatch can hide real paths, so `NO_STATIC_PATH` never means "safe".
* All discovered routes are treated as public (there is no authentication model).
* Framework packages (Flask/Werkzeug) are not modelled; their advisories stay undecided.
* Registry dispatch is class-insensitive, so PyYAML stays `POSSIBLY_REACHABLE` even for `safe_load`.
* Guard-point counts rank witnesses; they are not dominator proofs.
* Reachability is not exploitability. "Graph-checked" means the rebuilt graph shows the fix, not a security guarantee.

Design notes for each component are in [`docs/DESIGN.md`](docs/DESIGN.md).

## AI assistance disclosure

Built with GitHub Copilot CLI (Claude and GPT models) for design discussion, code generation and review, including a
multi-model panel review (two advisors and a judge) whose findings drove the correctness fixes, the closed loop and the
payload-bound approvals. The author is responsible for, and can explain, the graph model, queries, call-graph resolution,
architecture and trade-offs documented here and in `docs/DESIGN.md`.

## License

MIT
