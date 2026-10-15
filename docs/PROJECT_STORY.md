# Project story: what we built, why, and what we got

This is the long-form record of the Reachable project for Graph Hacks (WeMakeDevs × FalkorDB), Track 1. It explains the
decisions, the reasons behind them, the bugs we hit, what an independent panel said about it, and the results.
Everything here can be checked against the code, the tests and `make demo`.

---

## 1. The hackathon and the constraints

| Item | Detail |
|---|---|
| Event | Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB), online |
| Window | Oct 15 2026 12:01 AM IST → Oct 18 2026 11:59 PM IST |
| Team | Solo (Anurag), Python, Gemini API key |
| Tracks | 1. Agents That Act on Connected Data · 2. Agent Memory and Coordination · 3. Company Brain |
| Prizes | One winner per track (iPhone 18 Pro / PS5 with GTA VI / M6 Mac mini), blog-post side prize (AirPods 5), t-shirts for top 50 teams |
| Hard rule | FalkorDB must be the *primary* database and the product must stop working without the graph; a visualization over ordinary storage does not count |
| Submission | Public repo, README with setup and graph model, the Cypher/graph algorithms used, demo video, live deployment (preferred), declared track, AI-assistance disclosure |
| Judging | Criteria are not published. We assumed: graph essentiality/depth, track fit, innovation, technical execution, working demo, completeness, explainability |

**Why we chose one track.** Spreading across all three would have produced three shallow projects. We chose to be the
best answer to a single track and left the other two alone.

---

## 2. How we chose the idea

Two advisors (GPT-6 Astra and Claude Opus 5.5) were briefed independently, then critiqued each other.

* **Astra proposed "RecallOps"**: a food-recall agent tracing a contaminated ingredient through lot lineage to shipments.
* **Opus proposed "Reachable"**: an agent that decides which CVEs a public endpoint can actually reach, using a call graph.

Both picked Track 1. In the cross-critique:

* Opus argued RecallOps was weak on graph essentiality (one recursive SQL query or `networkx` could replace it), ran on
  synthetic data, and its "late manifest" demo moment is what any traceability dashboard does.
* Astra argued Reachable was risky for a solo dev (Python call graphs, CVE→function mapping), could overclaim safety, and
  that "no path found" is not proof of safety.

**Decision: Reachable, scoped down, with Astra's honesty rules.** Reasons:

1. It uses real data (real PyPI sources, real OSV advisories, real fix commits) instead of synthetic fixtures.
2. The graph does work SQL does poorly: a path query across code, dependency and advisory layers, with confidence encoded
   in the schema.
3. Gemini has a real job (reading fix-commit diffs), not decoration.
4. Astra's rule adopted: **never claim "safe"**. Verdicts are `REACHABLE`, `POSSIBLY_REACHABLE`, `NEEDS_REVIEW`,
   `NO_STATIC_PATH`, and the last one explicitly does not mean safe.
5. A fallback gate was agreed: if the AST call graph and the CVE→function mapping both failed in the first hours, switch
   to RecallOps. They did not fail.

---

## 3. Spikes: de-risking before building

| Spike | Finding | Consequence |
|---|---|---|
| FalkorDB query syntax | `shortestPath` is only allowed in `RETURN`/`WITH`, not `MATCH`; variable-length paths, `algo.SPpaths`, `algo.pageRank`, `algo.betweenness` all work from the Python client | Verdict queries use `WITH ... shortestPath(...) AS p WHERE p IS NOT NULL` |
| AST call graph | A resolver handling imports, re-exports, class hierarchy and local type inference reaches exact paths into real packages (e.g. `GET /preview` → `requests` → `urllib3.parse_url`) in a fraction of a second | Feasible for one person in a day |
| Runtime dispatch (PyYAML) | `construct_object` calls a constructor looked up from a registry at runtime; static analysis stops there | Added a labelled registry-dispatch heuristic (`MAYBE_CALLS`) rather than hiding the gap |
| CVE → vulnerable function | Git hunk headers name the *wrong* enclosing function (for PyYAML the nearest header was an unrelated neighbouring function, because the real change is a module-level registration), so a deterministic step alone is not enough | Deterministic candidates from the diff (matched by line text, so version drift is irrelevant), then **Gemini picks and explains**; one mapping is human-curated and labelled `curated` |
| Real advisory volume | The six pinned packages yield 33 de-duplicated advisories; nearly all carry fix commits | A realistic "pip-audit says 33" story, not a contrived one |

---

## 4. What was built (architecture and why each piece exists)

```
demo_app/vulnshop + pinned vulnerable deps (real PyPI sources, parsed with ast, never run)
OSV advisories + fix commits ──> callgraph.py / ingest.py ──> FalkorDB (3 layers)
                                       symbols.py: fix commit -> candidate functions -> Gemini picks
analysis.py   shortestPath verdicts, EVIDENCE, guard points      planner.py    version-aware plan
verify.py     scratch graph + gates                              actions.py    payload-bound approvals
agent.py      Gemini tool-calling agent        mcp_server.py   same tools over MCP (no execute)
streamlit_app.py  UI      guard.py  read-only public mode enforced in the backend
```

### The graph (three layers in one FalkorDB graph)
* **Code:** `Endpoint → Function → CALLS/MAYBE_CALLS/REFERENCES → Function`, extracted with `ast` from the app *and* the
  real source of its dependencies.
* **Supply chain:** `Repo/PackageVersion -[:DEPENDS_ON {spec}]-> PackageVersion`, carrying requirement specifiers such
  as `<1.27,>=1.21.1`.
* **Advisories:** `Vuln -[:AFFECTS]-> PackageVersion` and `Vuln -[:VULNERABLE_SYMBOL]-> Function`.

**Why confidence is a relationship *type*.** A call resolved through imports is `CALLS`; an attribute call on an
unresolved receiver or registry dispatch is `MAYBE_CALLS`; a function used as a value is `REFERENCES`. Then "provable"
and "plausible" are just two Cypher queries over different edge types, and the answer is explainable.

### Verdicts
`shortestPath` from every public endpoint to the vulnerable function. Exact-only path → `REACHABLE`; path needing weak
edges → `POSSIBLY_REACHABLE`; unanalyzed package / no known symbol / symbol only referenced in a registry →
`NEEDS_REVIEW`; otherwise `NO_STATIC_PATH`. The witness path is stored on the `Finding` itself (and as `EVIDENCE`
edges), so it survives later what-if re-ingests.

### Gemini's role (and its limits)
1. Pick the vulnerable function from a fix-commit diff plus a deterministic candidate list (structured JSON output).
2. Drive a tool-calling agent over the graph tools.

It never decides reachability and cannot execute actions. Without an API key an offline mode answers from the same tools.

### The agent's tools
`list_findings`, `explain_finding`, `recommend_fixes`, `plan_remediation`, `verify_remediation`, `supply_chain_hubs`
(betweenness), `query_graph` (read-only Cypher), `propose_fix_pr`, `propose_issue`, `propose_vex`. All proposals; none
execute.

### Interfaces
Streamlit UI (Triage & proof, Graph-checked fix, Agent, Actions, Graph & Cypher), a CLI, and an MCP server.

---

## 5. Problems we hit and how we fixed them

| Problem | Cause | Fix |
|---|---|---|
| Docker daemon not running | Docker Desktop was closed | Started it, pulled `falkordb/falkordb` |
| `.env` appeared empty | Editor changes were unsaved (file was 0 bytes) | Re-checked after save; never printed secrets |
| Gemini 404 on `gemini-2.5-flash` | Model no longer available to new accounts | Default moved to `gemini-3.5-flash` |
| Gemini 429 | Free tier of `gemini-3.5-flash` allows 20 requests/day | **Fallback model chain** (`llm.py`) that skips exhausted models |
| Agent claimed tool errors | `from __future__ import annotations` made tool signatures strings and broke the SDK's argument conversion | Removed that import from `agent.py` |
| Heuristic symbol picks after some Gemini failures | Transient 429/5xx | Retries with backoff; failure reason kept in the rationale |
| Streamlit Cloud could not connect | Secrets did not reach the app; the cloud app also ran an older commit | Config now reads `st.secrets` directly (also nested/lowercase keys) and the UI shows which secret *names* it sees |
| Approval result vanished | `st.rerun()` right after Approve discarded the message | Result kept in `session_state` |
| Path graph unreadable | Left-to-right layout shrank a 10-node chain | Top-down layout, compact labels, dark theme; verified by screenshot |
| First-run Streamlit prompt killed `make run` | Interactive email prompt | `--server.headless true` |
| Local app lost DB credentials | Stale server environment | Restarted with a clean environment |
| Deprecated Streamlit APIs | `components.html`, `use_container_width` | Moved to `st.iframe` / `width="stretch"` |

---

## 6. The panel review (the most important decision point)

After the first working version we convened a panel: **Astra** and an **Opus 5.5 strategist** as advisors, a second
**Opus 5.5 as an independent judge** (blind in round 1), and the lead engineer (this assistant) participating.

### Round 1 verdicts
* Astra: ~12% to win as-is. Opus strategist: ~12% as-is, 25-30% with fixes. Judge: 7/10, ~15% to be #1, ~45% top-3.
* **Verified real defects** (we reproduced three ourselves before acting):
  1. The flagship PyYAML witness path was **false**: it started with a bogus edge from Flask's `request.get_data()` to a
     PyYAML method (name-guessing across packages the caller never imports).
  2. The upgrade advice was **impossible**: it recommended `urllib3 2.6.3` while `requests 2.25.1` requires
     `urllib3<1.27`; the constraint was already in the graph but unused.
  3. The Agent tab's selected prompt button **re-sent on every rerun**.
* Other findings: VEX said `not_affected` for "no path found" (contradicting our own disclaimer); the stale-plan check only
  compared verdict labels; a what-if erased historical evidence; shared public graph could be mutated by any visitor;
  tests could hit the cloud DB or file real GitHub issues if `.env` pointed there; README overclaimed in places
  ("one traversal", "assigned" issues, betweenness as a reason the graph is essential).

### Round 2 and the final ruling
We asked how to spend ~20 hours of build time (plus ~10 for video/blog/buffer) with many Track 1 competitors. Decisions:

| Question | Ruling | Why |
|---|---|---|
| Closed-loop verified fix vs real open-source target | **Closed loop** | Strongest "agent that acts" story; a rushed real-repo target adds new false paths. Judges remember a story ("the agent caught its own mistake"), not which repo was ingested |
| MCP | In, **thin and late**, max 1h, no execute tool | The track hint mentions MCP; but correctness and the loop come first |
| Public deployment | **Read-only, enforced in the backend** | Per-visitor graph copies add memory/cleanup risk; a recorded closed-loop result is deterministic |
| Flask/Werkzeug modelling | **Out** | Costly; instead state "framework packages not modelled" |
| Approval binding | Payload + graph **snapshot**, not just a witness hash | A hash of the witness alone can't detect a changed target, diff or pins |
| Differentiator | Evidence-bound action: "here is the dependency-source witness authorizing this action; change the graph and the authorization expires" | Nobody else in a crowded track is likely to have this |

Judge's odds: ~25% for #1 and ~58% for top-3 if the plan is executed; ~13% for #1 if only correctness is fixed and the
closed loop skipped (the wow factor is gone).

**Hard stop rules adopted:** feature freeze Oct 18 12:00 IST; time-box the closed loop (met); every number in the pitch
must be reproducible by `make demo`.

---

## 7. What we implemented from the ruling

1. **Call-graph correctness.** Name-guess edges only into packages the calling module imports, never from receivers
   rooted in an unanalyzed import; locally bound names never fall back to module-level functions; regression tests with
   synthetic fixtures plus a golden-path test (`parse_settings → yaml.full_load → yaml.load`, exact calls).
2. **Honesty fixes.** Conservative VEX (`under_investigation` for no static path), durable witness paths on findings,
   one-click-one-request example buttons, hermetic tests (forced to localhost, GitHub filing off), overclaims removed.
3. **Version-aware planner.** Reads `spec` from `DEPENDS_ON` edges, rejects an impossible bump *on its own* with the
   reason, searches release metadata for the smallest compatible co-upgrade, then runs a pin-consistency check.
4. **Closed loop.** Builds the candidate pins into a private **scratch graph** (real sources, real advisories), re-triages,
   and applies four gates: *analysis coverage retained*, *constraints consistent*, *exposure decreases*, *no new
   exposure*. Coverage is checked first, and if it drops the status is `UNVERIFIED`, never "fixed".
5. **Evidence-bound approvals + real draft PR.** Each action stores a canonical payload (target, content hashes, graph
   snapshot hash, witness path or pins) and its SHA-256. Execution checks tampering, rebuilds the payload from the live
   graph, and aborts on any difference. The draft-PR action creates a branch, commits the new pins and opens a draft PR
   whose body is the gate report.
6. **Read-only public mode.** `REACHABLE_PUBLIC=true` makes every mutating backend function raise; the UI shows recorded
   results with snapshot ids.
7. **MCP server.** Read tools, `verify_remediation`, `propose_fix_pr`; deliberately no execute tool.
8. **`make demo`, integration test, docs, MIT license, repo description/topics.**

---

## 8. Results

### Headline numbers (all reproduced by `make demo` from a clean ingest)

| Metric | Value |
|---|---|
| Advisories (de-duplicated OSV hits for 6 pinned packages) | **33** |
| Verdicts | **4** `REACHABLE`, **7** `POSSIBLY_REACHABLE`, **18** `NEEDS_REVIEW`, **4** `NO_STATIC_PATH` |
| Exposed | **11** |
| Flagship path (GHSA-q2q7-5pp4-w6pg, urllib3) | 8 hops, exact calls only: `link_preview → fetch_preview → requests.get → … → prepare_url → urllib3.parse_url` |
| Naive plan | `urllib3 → 2.6.3` **rejected**: `requests 2.25.1 requires urllib3<1.27,>=1.21.1` |
| Resolved plan | PyYAML 5.3.1→5.4, requests 2.25.1→2.33.0, urllib3 1.26.4→2.6.3 (consistent) |
| Closed loop | **VERIFIED**: exposed 11 → 0, no new exposure, coverage retained (requests 248→258, PyYAML 365→366, urllib3 479→512, Jinja2 776→776 analysed functions) |
| What-if (remove the outbound HTTP call from `/preview`) | **10** verdicts flip; an action proposed beforehand is refused as stale |
| Graph size | 1,881 functions, ~2.7k call edges, 33 advisories |
| Symbol mapping | 17 advisories mapped by Gemini, 1 curated, 3 without a usable fix commit |
| Undecided (18) | 12 in Flask/Werkzeug (not modelled), 3 Jinja2 filters only referenced in a registry, 3 with no identifiable vulnerable function |
| Tests | 39 passing (call-graph regression, pipeline, planner, closed loop, approvals, public mode, MCP client, UI, demo) |

### Verified end to end in a real browser (Playwright + Chrome)
* Triage, Graph-checked fix, Agent, Actions tabs render correctly with the numbers above.
* Real **draft PR** created through the UI: only `demo_app/requirements.txt` changed (3 lines), body was the gate report.
  (The test PR was then closed and its branch deleted so the demo recording starts clean.)
* what-if → approve → **refused as stale**, and **zero** GitHub issues were filed.
* Public mode: no mutating controls; the agent's attempts to propose actions wrote nothing.
* Agent with live Gemini now runs `recommend_fixes → verify_remediation → propose_fix_pr` on its own and explains the
  rejected bump.

---

## 9. Why FalkorDB is essential here (the answer to "couldn't networkx do this?")

For the raw path search alone, yes. What makes the graph the product:

* The verdict is a path query over code + dependency + advisory nodes in one model; trust is encoded in the schema.
* Findings keep their evidence; approvals bind to a snapshot of the graph; a *rebuilt scratch graph* is how a fix is
  checked; version constraints are edges the planner reasons over.
* Remove FalkorDB and there are no verdicts, no evidence, no verification and no action authorization; the app fails
  closed.

We deliberately did **not** lean on `algo.betweenness` (7-node dependency graph) as a reason the graph matters; it remains
a tool, nothing more.

---

## 10. Known limits (stated in the README on purpose)

* Static Python call graphs are approximate; `NO_STATIC_PATH` never means safe.
* All routes are treated as public (no authentication model).
* Flask/Werkzeug are not modelled, so their advisories stay undecided.
* Registry dispatch is class-insensitive, so PyYAML stays `POSSIBLY_REACHABLE` even for `safe_load`.
* Guard-point counts rank witnesses; they are not dominator proofs.
* The target app is small and self-written; the evidence for generality is the real dependency sources, not the app.
* "Graph-checked" is not "secure"; reachability is not exploitability.

**What the panel suggested but we cut (and why):** a real open-source target (new false paths, schedule risk), Flask/Werkzeug
modelling, per-visitor graph copies, a second language ecosystem, more graph algorithms for show.

---

## 11. Likely questions from a judge, with honest answers

* *"Is the graph really needed?"* See section 9: the path query is replaceable; verdict + evidence + scratch-graph
  verification + evidence-bound authorization are not.
* *"Why can I trust a `REACHABLE`?"* It is an exact-edge path, stored, from a public endpoint into the dependency's
  real source; open the witness in the UI.
* *"Why `POSSIBLY_REACHABLE` for PyYAML?"* Two honest weak links: `yaml.load` instantiates a parameterized Loader class and
  `construct_object` dispatches through a runtime registry (`tests/test_callgraph.py` pins exactly these two).
* *"Did AI write this?"* AI assistants were used and are disclosed; the design is documented in `docs/DESIGN.md` and every
  fix traces to a test. The author must be able to explain `callgraph.py` resolution, the verdict queries, the planner,
  the gates and the approval binding.
* *"What if the upgraded packages are analysed less?"* The coverage gate returns `UNVERIFIED`, and a test forces that case.

---

## 12. Where things stand and what remains

**Done:** application, tests, docs, MIT license, repo metadata, real draft-PR path proven, read-only mode proven, graph
seeded in FalkorDB Cloud, everything pushed.

**Remaining (owner: Anurag):**
1. Add `REACHABLE_PUBLIC = "true"` to the Streamlit secrets and add the live URL to the README.
2. Record the ~2:30 demo (`docs/DEMO.md`); run `make seed` before each take; press Approve on the draft PR once.
3. Publish `docs/BLOG.md`; link video and blog in the README.
4. Submit on WeMakeDevs before **Oct 18, 11:59 PM IST**; feature freeze **Oct 18, 12:00 IST**.
5. Rehearse explaining the call-graph resolver, the verdict queries and the approval binding out loud.

**Reproduce everything:** `docker run -d -p 6379:6379 falkordb/falkordb`, `make setup`, `make seed`, `make run`, `make demo`,
`make test`.
