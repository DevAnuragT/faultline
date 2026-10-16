"""Build the three graph layers in FalkorDB: supply chain, code, advisories."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement

from . import callgraph, guard, sources
from .advisories import Vuln, query_pinned
from .config import CACHE, DEMO_APP
from .graph import Store, batches
from .symbols import extract_symbols

APP = "vulnshop"
PYPI_CACHE = CACHE / "pypi"
PYPI_CACHE.mkdir(parents=True, exist_ok=True)
PARSED_PACKAGES = ["PyYAML", "requests", "urllib3", "Jinja2"]  # sources we statically analyze

# What-if variants of the demo app: (file, old, new) text replacements applied to a temp copy.
VARIANTS: dict[str, dict] = {
    "baseline": {"label": "Baseline vulnshop", "edits": []},
    "no_preview_fetch": {
        "label": "Remove the outbound HTTP call from /preview",
        "edits": [("preview.py", "    resp = requests.get(url, timeout=5)\n    return resp.text[:500]",
                   "    return f\"preview unavailable for {url}\"")],
    },
}


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def parse_requirements(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([\w.]+)", line.strip())
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def pypi_requires(name: str, version: str) -> list[str]:
    cache = PYPI_CACHE / f"{_norm(name)}-{version}.json"
    if not cache.exists():
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as r:
            cache.write_text(json.dumps(json.load(r)["info"].get("requires_dist") or []))
    return json.loads(cache.read_text())


def parse_requires(requires: list[str]) -> list[tuple[str, str]]:
    """[(normalized dependency name, specifier text)] for requirements that apply without extras."""
    out = []
    for raw in requires:
        try:
            req = Requirement(raw)
        except Exception:
            continue
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        out.append((_norm(req.name), str(req.specifier)))
    return out


def dependency_edges(pins: dict[str, str]) -> list[tuple[str, str, str]]:
    """(dependent, dependency, specifier) for every dependency edge inside the pinned set."""
    by_norm = {_norm(n): n for n in pins}
    edges = []
    for name, ver in pins.items():
        for dep, spec in parse_requires(pypi_requires(name, ver)):
            if dep in by_norm:
                edges.append((name, by_norm[dep], spec))
    return edges


def prepare_app_dir(variant: str) -> Path:
    """Return a directory containing the demo app with the variant's edits applied."""
    if variant == "baseline":
        return DEMO_APP
    tmp = Path(tempfile.mkdtemp(prefix="vulnshop_"))
    shutil.copytree(DEMO_APP, tmp / "demo_app")
    root = tmp / "demo_app"
    for rel, old, new in VARIANTS[variant]["edits"]:
        f = root / APP / rel
        text = f.read_text()
        if old not in text:
            raise ValueError(f"variant edit for {rel} did not match")
        f.write_text(text.replace(old, new))
    return root


def build_callgraph(pins: dict[str, str], app_dir: Path) -> callgraph.CallGraph:
    roots = [(app_dir / APP, APP)]
    for name in PARSED_PACKAGES:
        for r in sources.package_roots(sources.fetch_package(name, pins[name])):
            roots.append((r, f"{name}=={pins[name]}"))
    return callgraph.build(roots)


def _pkg_key(name: str, pins: dict[str, str]) -> str:
    return f"{name}=={pins[name]}"


def load_supply_chain(st: Store, pins: dict[str, str], direct: list[str]) -> None:
    st.q("MERGE (:Repo {name:$n})", {"n": APP})
    rows = [{"key": _pkg_key(n, pins), "name": n, "version": v, "parsed": n in PARSED_PACKAGES,
             "direct": _norm(n) in {_norm(d) for d in direct}} for n, v in pins.items()]
    st.q("UNWIND $rows AS r CREATE (:PackageVersion {key:r.key, name:r.name, version:r.version, parsed:r.parsed, direct:r.direct})",
         {"rows": rows})
    st.q("UNWIND $rows AS r MATCH (a:Repo {name:$app}), (p:PackageVersion {key:r.key}) WHERE r.direct "
         "CREATE (a)-[:DEPENDS_ON {direct:true}]->(p)", {"rows": rows, "app": APP})
    deps = [{"a": _pkg_key(a, pins), "b": _pkg_key(b, pins), "spec": spec} for a, b, spec in dependency_edges(pins)]
    if deps:
        st.q("UNWIND $rows AS r MATCH (a:PackageVersion {key:r.a}), (b:PackageVersion {key:r.b}) "
             "CREATE (a)-[:DEPENDS_ON {direct:false, spec:r.spec}]->(b)", {"rows": deps})


def _module_rows(cg: callgraph.CallGraph, only_app: bool | None = None):
    rows = []
    for m in cg.mods.values():
        is_app = m.dist == APP
        if only_app is not None and is_app != only_app:
            continue
        rows.append({"name": m.name, "file": m.file, "dist": m.dist, "layer": "app" if is_app else "dep"})
    return rows


def _func_rows(cg: callgraph.CallGraph, only_app: bool | None = None):
    rows = []
    for f in cg.funcs.values():
        is_app = cg.mods[f.module].dist == APP
        if only_app is not None and is_app != only_app:
            continue
        rows.append({"q": f.qual, "name": f.name, "module": f.module, "file": f.file, "line": f.line,
                     "end": f.end_line, "dist": cg.mods[f.module].dist, "layer": "app" if is_app else "dep",
                     "body": f.is_module_body})
    return rows


def load_code(st: Store, cg: callgraph.CallGraph, pins: dict[str, str], only_app: bool | None = None) -> None:
    for chunk in batches(_module_rows(cg, only_app)):
        st.q("UNWIND $rows AS r CREATE (:Module {name:r.name, file:r.file, dist:r.dist, layer:r.layer})", {"rows": chunk})
    for chunk in batches(_func_rows(cg, only_app)):
        st.q("UNWIND $rows AS r CREATE (:Function {qualname:r.q, name:r.name, module:r.module, file:r.file, "
             "line:r.line, end_line:r.end, dist:r.dist, layer:r.layer, is_module_body:r.body})", {"rows": chunk})
    # Wire modules to functions, and modules to their package / repo.
    layers = ["app"] if only_app else (["dep"] if only_app is False else ["app", "dep"])
    st.q("MATCH (f:Function) WHERE f.layer IN $layers MATCH (m:Module {name:f.module}) "
         "WHERE NOT (m)-[:DEFINES]->(f) CREATE (m)-[:DEFINES]->(f)", {"layers": layers})
    if only_app is not False:
        st.q("MATCH (r:Repo {name:$app}), (m:Module {layer:'app'}) CREATE (r)-[:CONTAINS]->(m)", {"app": APP})
    if only_app is not True:
        st.q("MATCH (m:Module {layer:'dep'}), (p:PackageVersion {key:m.dist}) CREATE (p)-[:PROVIDES]->(m)")
    kinds = {callgraph.CALLS: [], callgraph.MAYBE_CALLS: [], callgraph.REFERENCES: []}
    for (s, d), k in cg.edges.items():
        src_app = cg.mods[cg.funcs[s].module].dist == APP
        if only_app is not None and src_app != only_app:
            continue
        kinds[k].append({"s": s, "d": d})
    for kind, rows in kinds.items():
        for chunk in batches(rows):
            st.q(f"UNWIND $rows AS r MATCH (a:Function {{qualname:r.s}}), (b:Function {{qualname:r.d}}) "
                 f"CREATE (a)-[:{kind}]->(b)", {"rows": chunk})
    load_endpoints(st, cg)


def load_endpoints(st: Store, cg: callgraph.CallGraph) -> None:
    rows = [{"id": f"{e.method} {e.route}", "route": e.route, "method": e.method, "func": e.func,
             "public": e.public} for e in cg.endpoints if cg.mods[cg.funcs[e.func].module].dist == APP]
    st.q("UNWIND $rows AS r MATCH (f:Function {qualname:r.func}) "
         "CREATE (e:Endpoint {id:r.id, route:r.route, method:r.method, public:r.public})-[:HANDLED_BY]->(f)", {"rows": rows})
    st.q("MATCH (r:Repo {name:$app}), (e:Endpoint) CREATE (r)-[:EXPOSES]->(e)", {"app": APP})


def load_owners(st: Store, app_dir: Path) -> None:
    rows = []
    for line in (app_dir / "CODEOWNERS").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            path, team = line.split()[:2]
            rows.append({"file": path, "team": team})
    st.q("UNWIND $rows AS r MERGE (t:Team {name:r.team}) WITH t, r MATCH (m:Module {layer:'app', file:r.file}) "
         "MERGE (t)-[:OWNS]->(m)", {"rows": rows})


def load_advisories(st: Store, vulns: list[Vuln], pins: dict[str, str], cg: callgraph.CallGraph,
                    progress=print) -> None:
    for i, v in enumerate(vulns, 1):
        sym = {"symbols": [], "confidence": 0, "source": "unparsed", "rationale": "Package source is not analyzed",
               "patch_url": None}
        if v.package in PARSED_PACKAGES:
            progress(f"[{i}/{len(vulns)}] mapping {v.id} ({v.package}) to vulnerable symbol")
            sym = extract_symbols(v, cg)
        st.q("MATCH (p:PackageVersion {key:$key}) CREATE (v:Vuln {id:$id, cve:$cve, summary:$summary, severity:$sev, "
             "package:$pkg, fixed_version:$fixed, patch_url:$patch, symbol_source:$src, symbol_rationale:$why, "
             "symbol_confidence:$conf})-[:AFFECTS]->(p)",
             {"key": _pkg_key(v.package, pins), "id": v.id, "cve": v.cve or "", "summary": v.summary[:240],
              "sev": v.severity, "pkg": v.package, "fixed": v.fixed_version or "", "patch": sym["patch_url"] or "",
              "src": sym["source"], "why": sym["rationale"], "conf": float(sym["confidence"])})
        for s in sym["symbols"]:
            st.q("MATCH (v:Vuln {id:$id}), (f:Function {qualname:$q}) "
                 "CREATE (v)-[:VULNERABLE_SYMBOL {source:$src, confidence:$conf}]->(f)",
                 {"id": v.id, "q": s, "src": sym["source"], "conf": float(sym["confidence"])})


def full_ingest(st: Store, variant: str = "baseline", progress=print, pins: dict[str, str] | None = None) -> dict:
    """Build every graph layer. `pins` overrides requirements.txt (used to build a candidate-fix scratch graph)."""
    guard.require_writable("Rebuilding the graph", st)
    app_dir = prepare_app_dir(variant)
    pins = pins or parse_requirements(DEMO_APP / "requirements.txt")
    direct = [l.strip() for l in (DEMO_APP / "requirements.in").read_text().split() if l.strip()]
    progress("building call graph from app + dependency sources")
    cg = build_callgraph(pins, app_dir)
    st.reset()
    st.ensure_indexes()
    progress("loading supply-chain layer")
    load_supply_chain(st, pins, direct)
    progress("loading code layer")
    load_code(st, cg, pins)
    load_owners(st, app_dir)
    progress("querying OSV advisories")
    vulns = query_pinned(pins)
    load_advisories(st, vulns, pins, cg, progress)
    return {"functions": len(cg.funcs), "edges": len(cg.edges), "vulns": len(vulns)}


def reload_app_layer(st: Store, variant: str) -> dict:
    """What-if: re-ingest only the app's code layer from a variant, leaving dependencies and advisories alone."""
    guard.require_writable("The what-if re-ingest")
    pins = parse_requirements(DEMO_APP / "requirements.txt")
    cg = build_callgraph(pins, prepare_app_dir(variant))
    st.q("MATCH (n) WHERE (n:Function OR n:Module) AND n.layer = 'app' DETACH DELETE n")
    st.q("MATCH (e:Endpoint) DETACH DELETE e")
    load_code(st, cg, pins, only_app=True)
    load_owners(st, prepare_app_dir(variant))
    return {"functions": len(cg.funcs), "edges": len(cg.edges)}
