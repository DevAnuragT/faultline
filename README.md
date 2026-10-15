# Reachable: graph-proven vulnerability triage

> `pip-audit` says you have 33 advisories. **Reachable** tells you which ones a public endpoint can actually
> reach, proves it with a call path stored in FalkorDB, and files the fix with the right owner.

**Hackathon:** Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB)
**Track:** 1. Agents That Act on Connected Data (single track by design)

## What it does

1. **Builds a three-layer graph in FalkorDB** from a real Python app and its real, pinned dependencies:
   code (endpoints → functions → calls, extracted statically with `ast` from the actual package sources),
   supply chain (repo → packages → transitive packages) and advisories (OSV records → affected package → vulnerable function).
2. **Decides reachability with graph queries**: a `shortestPath` from every public endpoint to the vulnerable function.
   Edges carry confidence as their *relationship type* (`CALLS` exact, `MAYBE_CALLS` name-guess / registry dispatch,
   `REFERENCES` value use), so Cypher can ask for "exact only" or "anything plausible".
3. **Persists the proof**: each `Finding` stores its witness path as `EVIDENCE` edges, so every verdict is auditable.
4. **A Gemini agent acts on the graph** through tools (`explain_finding`, `recommend_fixes`, `supply_chain_hubs`,
   `query_graph` (read-only Cypher), `propose_issue`, `propose_vex`). Gemini also reads fix-commit diffs to identify the
   vulnerable function. It never decides reachability.
5. **Actions are guarded**: the agent only *proposes*; a human approves; on approval code re-checks the graph and refuses
   stale plans, then files a GitHub issue assigned to the owning team (from `CODEOWNERS` in the graph) or writes an OpenVEX document.
6. **What-if**: change the app code, re-ingest only the app layer, and watch verdicts flip (removing the `requests.get`
   call from `/preview` turns 10 exposed advisories into `NO_STATIC_PATH`, and a previously proposed issue is refused as stale).

### Verdicts (honest by design)

| Verdict | Meaning |
|---|---|
| `REACHABLE` | Exact call path from a public endpoint to the vulnerable function |
| `POSSIBLY_REACHABLE` | Path exists but needs low-confidence edges (name-guess, registry dispatch). Example: PyYAML constructors chosen at runtime |
| `NEEDS_REVIEW` | Static analysis can't decide (framework package not modelled, no symbol known, or symbol only referenced in a registry such as Jinja2 filters) |
| `NO_STATIC_PATH` | No path found. **Not a proof of safety**; dynamic behaviour isn't modelled, and VEX output says so |

## Why FalkorDB is essential, not decoration

* The verdict **is** a path query over a code graph joined to advisories and packages in one traversal.
* Confidence is encoded in the schema (relationship types), so one engine answers both "provably" and "possibly".
* Aggregations over stored evidence rank **guard points** (which app function sits on the most exposed paths) and
  **upgrade leverage** (which single version bump clears the most exposed advisories).
* `algo.betweenness` finds supply-chain hubs. Ownership routing is a graph walk Module → Function.
* Remove FalkorDB and there is no verdict, no evidence, no owner routing and no action state; the app fails closed.

## Graph data model

```
(Repo)-[:CONTAINS]->(Module)-[:DEFINES]->(Function)
(Repo)-[:EXPOSES]->(Endpoint {route, method, public})-[:HANDLED_BY]->(Function)
(Repo)-[:DEPENDS_ON {direct}]->(PackageVersion {name, version, parsed})-[:DEPENDS_ON]->(PackageVersion)
(PackageVersion)-[:PROVIDES]->(Module)
(Function)-[:CALLS | :MAYBE_CALLS | :REFERENCES]->(Function)
(Team)-[:OWNS]->(Module)                                   # from CODEOWNERS
(Vuln {id, cve, severity, fixed_version, symbol_source, symbol_confidence})-[:AFFECTS]->(PackageVersion)
(Vuln)-[:VULNERABLE_SYMBOL {source, confidence}]->(Function)
(Run)-[:HAS_FINDING]->(Finding {verdict, reason})-[:FOR]->(Vuln)
(Finding)-[:VIA]->(Endpoint);  (Finding)-[:EVIDENCE {step, via}]->(Function)   # the witness path
(Action {type, status})-[:RESOLVES]->(Finding);  (Action)-[:ASSIGNED_TO]->(Team)
```

## Core Cypher (see `reachable/analysis.py`)

```cypher
// Exact proof: shortest all-CALLS path from any public endpoint to the vulnerable function
MATCH (v:Vuln {id:$id})-[:VULNERABLE_SYMBOL]->(f:Function), (e:Endpoint {public:true})-[:HANDLED_BY]->(h:Function)
WITH v, f, e, h, shortestPath((h)-[:CALLS*1..14]->(f)) AS p
WHERE p IS NOT NULL
RETURN e.id, [n IN nodes(p) | n.qualname], [r IN relationships(p) | type(r)] ORDER BY length(p) LIMIT 1

// Plausible path: allow low-confidence edges
... shortestPath((h)-[:CALLS|MAYBE_CALLS|REFERENCES*1..14]->(f)) ...

// Guard points: app functions on the most exposed witness paths
MATCH (r:Run {id:$run})-[:HAS_FINDING]->(f:Finding)-[:EVIDENCE]->(n:Function {layer:'app'})
WHERE f.verdict IN ['REACHABLE','POSSIBLY_REACHABLE']
RETURN n.qualname, count(DISTINCT f) AS findings ORDER BY findings DESC

// Supply-chain hubs
CALL algo.betweenness({nodeLabels:['PackageVersion','Repo'], relationshipTypes:['DEPENDS_ON']}) YIELD node, score
```

## Architecture

```
demo_app/vulnshop (Flask, pinned vulnerable deps)  ──┐
PyPI sdists/wheels (parsed with ast, never run)    ──┼─> callgraph.py ─> FalkorDB ─> analysis.py (verdicts, evidence)
OSV API + fix commits                              ──┘   (graph)          ^              |
                                                          symbols.py: patch -> candidate functions -> Gemini picks  |
                                                                         agent.py (Gemini tools) -> actions.py (approve, guard, execute)
                                                                                           └─ streamlit_app.py
```

Where Gemini is used: (1) choosing the vulnerable function from a fix-commit diff + deterministic candidate list
(structured JSON output, results cached in `data/cache/symbol_map.json`); (2) the function-calling triage agent.
Free-tier Gemini quotas are small, so calls walk a model fallback chain (`GEMINI_MODEL`, then `GEMINI_FALLBACK_MODELS`), skipping
models that are out of quota. Without `GEMINI_API_KEY` the app runs in an offline mode: heuristic symbol picking (labelled `heuristic`) and a
deterministic answerer over the same tools.

## Run it

```bash
docker run -d -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest      # or: docker compose up --build
uv venv --python 3.12 .venv && uv pip install -r requirements.txt     # any Python 3.11+
cp .env.example .env     # add GEMINI_API_KEY (optional but recommended); GITHUB_REPO=owner/repo to file real issues
                         # for a local FalkorDB leave FALKORDB_USERNAME / FALKORDB_PASSWORD empty
.venv/bin/streamlit run streamlit_app.py                               # first run builds the graph
```

CLI: `python -m reachable.cli ingest | triage | whatif no_preview_fetch | ask "what should we fix first?"`.
Tests (need FalkorDB running): `.venv/bin/python -m pytest tests`.
FalkorDB Browser at http://localhost:3000 lets you explore the graph (`MATCH p=(:Endpoint)-[:HANDLED_BY]->()-[:CALLS*1..8]->(:Function {name:'parse_url'}) RETURN p`).

## Data

Everything is public: the demo app is written for this project, dependencies are real PyPI releases at pinned
vulnerable versions, advisories come from the public OSV API, patches from public GitHub commits. No private data.
One symbol mapping (PyYAML CVE-2020-14343) is human-curated in `data/curated_symbols.json` because the upstream fix
only moves module-level constructor registrations; it is labelled `curated` in the UI.

## Known limitations

* Python call graphs are approximate: unresolved dynamic dispatch can hide real paths. That is why the product
  never claims safety, and why `NO_STATIC_PATH` is worded as "no path found".
* Flask/Werkzeug are not parsed (framework-driven dispatch), so their advisories are `NEEDS_REVIEW`.
* Registry dispatch is class-insensitive, so PyYAML stays `POSSIBLY_REACHABLE` even for `safe_load`.
* Reachability is not exploitability.

## AI assistance disclosure

Built with GitHub Copilot CLI (Claude and GPT models) for design discussion, code generation and review. The author
is responsible for and can explain the graph model, queries, architecture and trade-offs documented above.
