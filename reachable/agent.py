"""Gemini function-calling agent. Tools are graph queries; the model explains and proposes, it never decides
reachability and it cannot execute actions (humans approve those in the UI)."""

import re
import time

from . import actions, analysis, config, guard, llm, planner, verify
from .graph import Store

SYSTEM = """You are Reachable, a vulnerability triage agent. Your knowledge comes ONLY from tools that query a
FalkorDB graph linking public endpoints -> functions -> dependency functions -> advisories.
Rules:
- Always call tools to get facts; never guess verdicts. The verdict comes from the graph, not from you.
- Verdicts: REACHABLE (exact call path from a public endpoint), POSSIBLY_REACHABLE (path needs low-confidence edges),
  NEEDS_REVIEW (static analysis cannot decide), NO_STATIC_PATH (no path found).
- NO_STATIC_PATH is NOT a proof of safety (dynamic behaviour is not modelled). Never say a vulnerability is 'safe'.
- When explaining, cite the endpoint and quote the call path from explain_finding.
- Whenever the user asks what to fix, how to remediate, or what to prioritize: call recommend_fixes first. If its plan
  lists rejected bumps, say so explicitly (the reason comes from dependency constraints in the graph) and name the
  co-upgrade that resolves it. Then call verify_remediation to rebuild the graph with the candidate versions and check
  the gates, and report the status, exposure before/after and each gate. Only if the status is VERIFIED call
  propose_fix_pr. Never call a fix 'verified secure'; say 'graph-checked'. If the status is UNVERIFIED or REJECTED,
  explain why and do not propose a PR.
- You may PROPOSE actions (propose_fix_pr, propose_issue, propose_vex). Nothing is created, opened or filed until a
  human approves the proposal in the Actions tab: say "proposed" (never "created" or "opened") and give the action id.
- Be concise: short paragraphs or bullets, no filler."""

FORBIDDEN = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL\s+db\.|LOAD\s+CSV)\b", re.I)


class Toolbox:
    """Graph-backed tools exposed to the model. Every call is recorded in `trace` for the UI."""

    def __init__(self, st: Store) -> None:
        self.st = st
        self.trace: list[dict] = []

    def _log(self, name: str, args: dict, result) -> None:
        self.trace.append({"tool": name, "args": args, "result": result, "ts": time.time()})

    def list_findings(self, verdict: str = "") -> list[dict]:
        """List vulnerability findings from the latest triage run. Optional verdict filter:
        REACHABLE, POSSIBLY_REACHABLE, NEEDS_REVIEW or NO_STATIC_PATH."""
        rows = [{"vuln": f["vuln"], "cve": f["cve"], "package": f["package"], "version": f["version"],
                 "severity": f["severity"], "verdict": f["verdict"]}
                for f in analysis.latest_findings(self.st) if not verdict or f["verdict"] == verdict.upper()]
        self._log("list_findings", {"verdict": verdict}, f"{len(rows)} findings")
        return rows

    def explain_finding(self, vuln_id: str) -> dict:
        """Explain one finding: verdict, reasoning, the witness call path from the public endpoint to the
        vulnerable function (with edge confidence), how the vulnerable symbol was identified, and the owner."""
        f = next((x for x in analysis.latest_findings(self.st) if x["vuln"] == vuln_id), None)
        if not f:
            res = {"error": f"unknown vulnerability {vuln_id}"}
        else:
            path = analysis.witness_path(self.st, f["finding"])
            app_fns = [p["function"] for p in path if p["layer"] == "app" and not p["function"].endswith(".<module>")]
            res = {**f, "witness_path": [f"{p['function']} ({p['via']})" for p in path],
                   "owner": analysis.owner_of(self.st, app_fns[-1]) if app_fns else None,
                   "guard_point": app_fns[-1] if app_fns else None}
        self._log("explain_finding", {"vuln_id": vuln_id}, res)
        return res

    def recommend_fixes(self) -> dict:
        """Recommend fixes from the graph: the app functions on the most exposed paths (guard points) and a
        version-aware upgrade plan. The plan checks dependency constraints stored in the graph, rejects bumps that are
        impossible on their own (and says why) and finds the co-upgrades that make the set installable."""
        run = analysis.latest_findings(self.st)[0]["run"]
        p = planner.plan_remediation(self.st, run)
        res = {"guard_points": analysis.guard_points(self.st, run), "plan": p.as_dict()}
        self._log("recommend_fixes", {}, res)
        return res

    def supply_chain_hubs(self) -> list[dict]:
        """Betweenness centrality over the dependency graph: which packages many dependency paths run through."""
        res = analysis.supply_chain_chokepoints(self.st)
        self._log("supply_chain_hubs", {}, res)
        return res

    def query_graph(self, cypher: str) -> dict:
        """Run a READ-ONLY Cypher query against the graph (labels: Repo, Endpoint, Function, Module,
        PackageVersion, Vuln, Finding, Team; edges: HANDLED_BY, CALLS, MAYBE_CALLS, REFERENCES, AFFECTS,
        VULNERABLE_SYMBOL, DEPENDS_ON, EVIDENCE, OWNS). Returns at most 25 rows."""
        if FORBIDDEN.search(cypher):
            res = {"error": "only read-only queries are allowed"}
        else:
            try:
                header, rows = self.st.ro(cypher)
                res = {"columns": header, "rows": rows[:25]}
            except Exception as exc:
                res = {"error": str(exc)[:300]}
        self._log("query_graph", {"cypher": cypher}, res)
        return res

    def _guarded(self, name: str, args: dict, fn) -> dict:
        try:
            res = fn()
        except guard.ReadOnlyError as exc:
            res = {"ok": False, "error": str(exc)}
        self._log(name, args, res)
        return res

    def plan_remediation(self) -> dict:
        """Plan version bumps for the exposed advisories using the dependency constraints stored in the graph.
        Bumps that are impossible on their own are rejected with the reason, and a compatible co-upgrade is found."""
        res = planner.plan_remediation(self.st).as_dict()
        self._log("plan_remediation", {}, res)
        return res

    def verify_remediation(self) -> dict:
        """Check the planned upgrade BEFORE proposing it: build the candidate dependency versions into a private
        scratch graph, re-run triage there and compare to the baseline through gates (analysis coverage retained,
        constraints consistent, exposure decreases, no new exposure). Status: VERIFIED, REJECTED or UNVERIFIED
        (UNVERIFIED means analysis coverage dropped, so no success may be claimed). Takes several seconds."""
        def run():
            note = ""
            if config.PUBLIC_MODE:
                try:  # live, in a private scratch graph; nothing is written to the shared graph
                    r = verify.run_closed_loop_ephemeral(self.st, progress=lambda m: None)
                    note = "live run in a private scratch graph (public demo: result not persisted)"
                except verify.BusyError as exc:
                    r = verify.latest_remediation(self.st)
                    if not r:
                        raise guard.ReadOnlyError(str(exc))
                    note = f"recorded run shown ({exc})"
            else:
                r = verify.run_closed_loop(self.st, progress=lambda m: None)
            return {"id": r["id"], "status": r["status"], "gates": r["gates"], "before": r["before"], "after": r["after"],
                    "cleared": r["cleared"], "new_exposed": r["new_exposed"], "changes": r["plan"]["changes"],
                    "rejected_on_their_own": r["plan"]["rejected"], **({"note": note} if note else {})}
        return self._guarded("verify_remediation", {}, run)

    def propose_fix_pr(self) -> dict:
        """Propose (not open) a draft pull request for the latest VERIFIED remediation. Refused unless the
        remediation is VERIFIED against the current graph snapshot. A human must approve it."""
        return self._guarded("propose_fix_pr", {}, lambda: actions.propose_fix_pr(self.st))

    def propose_issue(self, vuln_id: str) -> dict:
        """Propose (not file) a tracking issue for an exposed finding, routed to the owning team.
        A human must approve it before anything is filed."""
        return self._guarded("propose_issue", {"vuln_id": vuln_id}, lambda: actions.propose_issue(self.st, vuln_id))

    def propose_vex(self) -> dict:
        """Propose an OpenVEX document covering the latest triage run. A human must approve it."""
        return self._guarded("propose_vex", {}, lambda: actions.propose_vex(self.st))

    def tools(self) -> list:
        return [self.list_findings, self.explain_finding, self.recommend_fixes, self.plan_remediation,
                self.verify_remediation, self.supply_chain_hubs, self.query_graph, self.propose_fix_pr,
                self.propose_issue, self.propose_vex]


class Agent:
    def __init__(self, st: Store) -> None:
        self.st = st
        self.box = Toolbox(st)
        self.chat = None
        self.model = ""
        self.live = bool(config.GEMINI_API_KEY)
        if self.live:
            from google import genai
            from google.genai import types

            self._types = types
            self._client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=60000))
            self._new_chat(llm.models()[0])

    def _new_chat(self, model: str, history=None) -> None:
        self.model = model
        self.chat = self._client.chats.create(
            model=model, history=history,
            config=self._types.GenerateContentConfig(system_instruction=SYSTEM, tools=self.box.tools(), temperature=0))

    def ask(self, message: str) -> tuple[str, list[dict]]:
        start = len(self.box.trace)
        if self.live:
            last: Exception | None = None
            for _ in range(len(config.GEMINI_FALLBACK_MODELS) + 1):
                try:
                    reply = self.chat.send_message(message).text or ""
                    return reply, self.box.trace[start:]
                except Exception as exc:
                    last = exc
                    if not llm.is_quota_error(exc):
                        break
                    llm.mark_exhausted(self.model)
                    self._new_chat(llm.models()[0], history=self.chat.get_history())
            note = f"(Gemini unavailable: {last.__class__.__name__}; answering from graph tools directly)\n\n"
            return note + self._offline(message), self.box.trace[start:]
        return self._offline(message), self.box.trace[start:]

    # ---- deterministic fallback so the product works without an API key
    def _offline(self, message: str) -> str:
        m = message.lower()
        vid = re.search(r"(GHSA-[\w-]+|CVE-\d+-\d+)", message, re.I)
        if vid:
            key = vid.group(1)
            if key.upper().startswith("CVE"):
                match = next((f for f in analysis.latest_findings(self.st) if (f["cve"] or "").upper() == key.upper()), None)
                key = match["vuln"] if match else key
            else:
                key = key.upper().replace("GHSA", "GHSA")
                match = next((f for f in analysis.latest_findings(self.st) if f["vuln"].lower() == key.lower()), None)
                key = match["vuln"] if match else key
            r = self.box.explain_finding(key)
            if "error" in r:
                return r["error"]
            return (f"**{r['vuln']}** ({r['package']} {r['version']}): **{r['verdict']}**\n\n{r['reason']}\n\n"
                    "Path:\n" + "\n".join(f"- `{p}`" for p in r["witness_path"]) +
                    (f"\n\nOwner: {r['owner']}" if r["owner"] else ""))
        if any(w in m for w in ("fix", "upgrade", "priorit", "recommend")):
            r = self.box.recommend_fixes()
            lines = [f"- Guard `{g['function']}` covers {g['findings']} exposed findings" for g in r["guard_points"][:3]]
            lines += [f"- Rejected: {x['reason']} (resolution: {x['resolution']})" for x in r["plan"]["rejected"]]
            lines += [f"- Upgrade `{c['package']}` {c['from']} -> {c['to']}: {c['reason']}" for c in r["plan"]["changes"]]
            return "Recommended actions:\n" + "\n".join(lines)
        if "issue" in m:
            out = [self.box.propose_issue(f["vuln"]) for f in analysis.latest_findings(self.st) if f["verdict"] == "REACHABLE"]
            return "Proposed issues (awaiting your approval in the Actions tab): " + ", ".join(
                o.get("action", o.get("error", "?")) for o in out)
        if "vex" in m:
            return f"Proposed VEX document: {self.box.propose_vex()}"
        findings = self.box.list_findings()
        counts: dict[str, int] = {}
        for f in findings:
            counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
        top = [f for f in findings if f["verdict"] == "REACHABLE"]
        return (f"{len(findings)} advisories triaged: " + ", ".join(f"{v} {n}" for v, n in counts.items()) +
                ".\n\nExposed now:\n" + "\n".join(f"- {f['vuln']} {f['package']} ({f['severity']})" for f in top))
