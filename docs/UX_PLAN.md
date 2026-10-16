# Faultline for SREs: UX plan

Faultline's stance does not change: the graph decides what to break and what to fix, a person approves, and every number on screen is a query over the trace graph. This plan makes that stance fast to use under pressure and easy to review afterwards.

## Personas and jobs to be done

**On-call SRE, paged.** Has five minutes and one question at a time:
1. Is it broken right now, compared with normal?
2. Where: which endpoint, which hop, which line of code?
3. What changed since the last good window?
4. What do I do next that is safe and reversible?
5. How do I hand this to the channel: a link, an id, a summary?

Works from the keyboard, pastes links into Slack or incident.io, and copies ids into other tools.

**Reliability reviewer (weekly review, postmortem owner).** Has an hour and needs the record:
1. What did the last experiments teach us, and did the prediction hold?
2. Which fixes were kept, and which gates failed?
3. What should we break or fix next, and why that one?
4. What do I paste into the review doc?

Reads dense tables and sorts them, compares windows, and exports Markdown.

## How real tools answer these questions

- **Grafana / Datadog:** a status line before detail, deltas against a baseline on every headline number, a time range that lives in the URL so links reproduce the view.
- **Honeycomb:** go from an aggregate to one trace by id, and from one trace back to the aggregate.
- **PagerDuty / incident.io:** incidents read like runbook entries (impact, started, what changed, where to look, next action), with copyable ids and one-click actions.
- **All of them:** keyboard navigation (`g` then a letter, `/`, `?`), sortable tables, compact density for big screens.

## Prioritised improvements

| # | Improvement | Need it serves | Front end only? |
|---|---|---|---|
| 1 | **Status banner** for the selected window against its baseline (the steady window before it): Healthy, Degraded or Failing, using the same thresholds as incident detection (failures up more than 5 points, or p95 above 1.5 × baseline). | Is it broken? | Yes, from `/api/windows` + `/api/compare` |
| 2 | **Baseline deltas on the KPI tiles** (p50, p95, failure rate). | What changed? | Yes |
| 3 | **Deep links**: `#view?w=<window>&t=<trace>` restores the view, window and trace; a Copy link button and the `y` key. | Hand-off | Yes |
| 4 | **Copy buttons** on window ids, trace ids, action ids and evidence hashes. | Hand-off, cross-tool search | Yes |
| 5 | **Next steps on Findings**: the concrete action the graph supports now (file an incident, propose the fix, add the timeout, test the riskiest dependency), one click each, with "awaiting approval" when it is already proposed. | What do I do next? | Yes, from `/api/planner`, `/api/incidents`, `/api/actions` |
| 6 | **Runbook-style incident cards**: impact, window span, baseline, what moved, where to look, worst request (opens in Trace), suggested action. | Where, what next | Yes |
| 7 | **Trace lookup**: open any trace by id, plus the slowest requests in the window, each linkable. | Where, aggregate to trace | Yes, via `/api/trace/{id}` and a read-only Cypher query |
| 8 | **Keyboard shortcuts** (`g f/t/e/a/p/g`, `/`, `w`, `[` `]`, `y`, `?`) with a help overlay. | Speed | Yes |
| 9 | **Sortable tables** (owners, guardrails, planner). | Review | Yes |
| 10 | **Markdown export** of the window report and the ledger to the clipboard. | Postmortem, review doc | Yes |
| 11 | **Compact density**, remembered per browser. | Dense data on big screens | Yes |
| 12 | Compare defaults to *baseline → selected* instead of first → last window. | What changed? | Yes |
| 13 | "Show the query" on every finding, opening it in the Cypher console. | Every number is a query | Needs backend |
| 14 | SLO line on the banner (target p95 and error budget burn). | Is it broken against what we promised? | Needs backend |
| 15 | Real incident start and end times, and a window timeline. | When did it start? | Needs backend |
| 16 | Server-ranked next actions. Today the front end infers them from the planner, guardrails and incidents. | What next, one source of truth | Needs backend |

## Backend changes needed (for 13–16 and to remove client-side inference)

1. `GET /api/windows`: per window add `kind` (`"steady"` or `"experiment"`), `baseline` (id of the preceding steady window, or `null`), `start_us` and `end_us` (already on the `Window` node), and `health: {op, p50_ms, p95_ms, fail_rate, traces}` for the root endpoint.
2. `GET /api/status?window=<id>`: `{status: "ok"|"degraded"|"failing"|"baseline", baseline, reasons: [str], deltas: {p50_pct, p95_pct, fail_rate_pts}}`. This is computed with `analysis.incident`'s thresholds so the banner and the incident list can never disagree.
3. `GET /api/overview`: each finding gets `cypher` (the query that produced it) and `params`.
4. `GET /api/traces?window=<id>&order=duration|start&errors=true&limit=20`: `[{id, root, duration_ms, status, errors, start_us}]`. Today this is a read-only Cypher query from the browser.
5. `GET /api/incidents`: add `started_us` and `ended_us` (first and last trace in the window), `baseline_label`, and `suggested: {type, payload, title, why}`.
6. `GET /api/planner`: add `actions: [{type, payload, title, why, priority}]`, the ranked next actions across fix, guardrail, experiment and issue.
7. `GET /api/meta`: add `slo: {endpoint, p95_ms, fail_rate}` from config, so the banner can say "within SLO" rather than only "within baseline".

## Implemented now (front end only)

Items 1–12. Every new control keeps the existing ids, data attributes and fetch logic. Public mode keeps write buttons disabled and says why. The Agent view also no longer scrolls the page: the transcript scrolls inside a panel that fills the viewport, with the composer pinned at the bottom.

## Deliberately not done

- **Auto-refresh or live tailing.** Windows are batch ingests, so polling would only add noise. Revisit once ingestion is continuous.
- **Severity colours beyond good / warn / critical.** One more hue would weaken the scan.
- **Inline approval from the Findings next steps.** Approval stays a deliberate trip to Approvals, where the evidence hash is shown.
