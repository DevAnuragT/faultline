# Demo script (about 2.5 min)

Setup: FalkorDB running, `streamlit run streamlit_app.py`, graph already built, FalkorDB Browser open on :3000.

1. **0:00 The problem (15s).** "Dependency scanners list 33 advisories for this small Flask app. Which ones can an
   attacker actually reach?" Show the metric row: 33 advisories, 3 reachable, 8 possibly, 18 needs review.
2. **0:15 The proof (40s).** Triage tab, pick GHSA-q2q7-5pp4-w6pg (urllib3). The graph shows
   `GET /preview → fetch_preview → requests.get → … → prepare_url → parse_url`, built from AST of the real packages.
   Point out dashed amber edges for low-confidence links versus solid exact calls.
3. **0:55 The decoy (15s).** Pick a Jinja2 advisory: the app imports Jinja2 but the vulnerable filter is only registered
   in a dict, so it is NEEDS_REVIEW, not a false alarm and not a false all-clear.
4. **1:10 The agent acts (35s).** Agent tab: "What should we fix first?" Show tool calls (`recommend_fixes`), the guard
   point `vulnshop.preview.fetch_preview` covering the most paths, and the single `requests` bump that clears the most.
   Then "Propose issues for everything reachable." Actions tab: issues assigned to `@vulnshop/growth`, waiting for approval.
5. **1:45 Wow: the verdict flips (35s).** Sidebar: choose "Remove the outbound HTTP call from /preview", re-ingest.
   Banner shows 10 advisories flip to NO_STATIC_PATH. Click Approve on the old issue: refused as a stale plan.
6. **2:20 Why FalkorDB (10s).** Graph & Cypher tab: the `shortestPath` query that produced every verdict, and
   `algo.betweenness` hubs. "Remove the graph and the verdicts, evidence and routing disappear."

Reset between takes: sidebar → baseline → Re-ingest, or `python -m reachable.cli whatif baseline`.
