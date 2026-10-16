"""Build the three graph layers in FalkorDB: supply chain, code, advisories."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement

from . import callgraph, guard, sources, targets
from .advisories import Vuln, query_pinned
from .config import CACHE, DEMO_APP
from .targets import Target
from .graph import Store, batches
from .symbols import extract_symbols

PYPI_CACHE = CACHE / "pypi"
PYPI_CACHE.mkdir(parents=True, exist_ok=True)

VARIANTS = targets.DEMO_VARIANTS  # what-if variants of the demo app (other targets only have a baseline)


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


def prepare_app_dir(variant: str, target: Target | None = None) -> Path:
    """Directory containing the app's package dirs, with the variant's edits applied to a temp copy."""
    target = target or targets.get(None)
    if variant == "baseline" or not target.variants.get(variant, {}).get("edits"):
        return target.root
    tmp = Path(tempfile.mkdtemp(prefix=f"{target.name}_"))
    shutil.copytree(target.root, tmp / "app_copy")
    root = tmp / "app_copy"
    for rel, old, new in target.variants[variant]["edits"]:
        f = root / target.packages[0] / rel
        text = f.read_text()
        if old not in text:
            raise ValueError(f"variant edit for {rel} did not match")
        f.write_text(text.replace(old, new))
    return root


def parsed_packages(target: Target, pins: dict[str, str], vulns: list[Vuln]) -> list[str]:
    """Which dependency sources we statically analyze: the target's fixed list, or every pin that has advisories."""
    if target.parsed is not None:
        return [p for p in target.parsed if p in pins]
    return sorted({v.package for v in vulns if v.package in pins})


def build_callgraph(pins: dict[str, str], app_dir: Path, target: Target | None = None, parsed: list[str] | None = None,
                    progress=lambda *_: None) -> tuple[callgraph.CallGraph, list[str]]:
    """Returns (call graph, packages actually parsed). Packages whose source can't be fetched are skipped, not fatal."""
    target = target or targets.get(None)
    roots = [(app_dir / pkg, target.name) for pkg in target.packages]
    done: list[str] = []
    for name in (parsed if parsed is not None else list(target.parsed or [])):
        try:
            found = sources.package_roots(sources.fetch_package(name, pins[name]))
        except Exception as exc:
            progress(f"  skipping {name}=={pins[name]}: {exc.__class__.__name__}")
            continue
        if not found:
            continue
        done.append(name)
        roots += [(r, f"{name}=={pins[name]}") for r in found]
    return callgraph.build(roots), done


def _pkg_key(name: str, pins: dict[str, str]) -> str:
    return f"{name}=={pins[name]}"


def load_supply_chain(st: Store, pins: dict[str, str], direct: list[str], target: Target, parsed: list[str]) -> None:
    st.q("MERGE (:Repo {name:$n})", {"n": target.name})
    rows = [{"key": _pkg_key(n, pins), "name": n, "version": v, "parsed": n in parsed,
             "direct": _norm(n) in {_norm(d) for d in direct}} for n, v in pins.items()]
    st.q("UNWIND $rows AS r CREATE (:PackageVersion {key:r.key, name:r.name, version:r.version, parsed:r.parsed, direct:r.direct})",
         {"rows": rows})
    st.q("UNWIND $rows AS r MATCH (a:Repo {name:$app}), (p:PackageVersion {key:r.key}) WHERE r.direct "
         "CREATE (a)-[:DEPENDS_ON {direct:true}]->(p)", {"rows": rows, "app": target.name})
    deps = [{"a": _pkg_key(a, pins), "b": _pkg_key(b, pins), "spec": spec} for a, b, spec in dependency_edges(pins)]
    if deps:
        st.q("UNWIND $rows AS r MATCH (a:PackageVersion {key:r.a}), (b:PackageVersion {key:r.b}) "
             "CREATE (a)-[:DEPENDS_ON {direct:false, spec:r.spec}]->(b)", {"rows": deps})


def _module_rows(cg: callgraph.CallGraph, app: str, only_app: bool | None = None):
    rows = []
    for m in cg.mods.values():
        is_app = m.dist == app
        if only_app is not None and is_app != only_app:
            continue
        rows.append({"name": m.name, "file": m.file, "dist": m.dist, "layer": "app" if is_app else "dep"})
    return rows


def _func_rows(cg: callgraph.CallGraph, app: str, only_app: bool | None = None):
    rows = []
    for f in cg.funcs.values():
        is_app = cg.mods[f.module].dist == app
        if only_app is not None and is_app != only_app:
            continue
        rows.append({"q": f.qual, "name": f.name, "module": f.module, "file": f.file, "line": f.line,
                     "end": f.end_line, "dist": cg.mods[f.module].dist, "layer": "app" if is_app else "dep",
                     "body": f.is_module_body})
    return rows


def load_code(st: Store, cg: callgraph.CallGraph, pins: dict[str, str], target: Target,
              only_app: bool | None = None) -> None:
    app = target.name
    for chunk in batches(_module_rows(cg, app, only_app)):
        st.q("UNWIND $rows AS r CREATE (:Module {name:r.name, file:r.file, dist:r.dist, layer:r.layer})", {"rows": chunk})
    for chunk in batches(_func_rows(cg, app, only_app)):
        st.q("UNWIND $rows AS r CREATE (:Function {qualname:r.q, name:r.name, module:r.module, file:r.file, "
             "line:r.line, end_line:r.end, dist:r.dist, layer:r.layer, is_module_body:r.body})", {"rows": chunk})
    # Wire modules to functions, and modules to their package / repo.
    layers = ["app"] if only_app else (["dep"] if only_app is False else ["app", "dep"])
    st.q("MATCH (f:Function) WHERE f.layer IN $layers MATCH (m:Module {name:f.module}) "
         "WHERE NOT (m)-[:DEFINES]->(f) CREATE (m)-[:DEFINES]->(f)", {"layers": layers})
    if only_app is not False:
        st.q("MATCH (r:Repo {name:$app}), (m:Module {layer:'app'}) CREATE (r)-[:CONTAINS]->(m)", {"app": app})
    if only_app is not True:
        st.q("MATCH (m:Module {layer:'dep'}), (p:PackageVersion {key:m.dist}) CREATE (p)-[:PROVIDES]->(m)")
    kinds = {callgraph.CALLS: [], callgraph.MAYBE_CALLS: [], callgraph.REFERENCES: []}
    for (s, d), k in cg.edges.items():
        src_app = cg.mods[cg.funcs[s].module].dist == app
        if only_app is not None and src_app != only_app:
            continue
        kinds[k].append({"s": s, "d": d})
    for kind, rows in kinds.items():
        for chunk in batches(rows):
            st.q(f"UNWIND $rows AS r MATCH (a:Function {{qualname:r.s}}), (b:Function {{qualname:r.d}}) "
                 f"CREATE (a)-[:{kind}]->(b)", {"rows": chunk})
    load_endpoints(st, cg, app)


def load_endpoints(st: Store, cg: callgraph.CallGraph, app: str) -> None:
    rows = [{"id": f"{e.method} {e.route}", "route": e.route, "method": e.method, "func": e.func,
             "public": e.public} for e in cg.endpoints if cg.mods[cg.funcs[e.func].module].dist == app]
    st.q("UNWIND $rows AS r MATCH (f:Function {qualname:r.func}) "
         "CREATE (e:Endpoint {id:r.id, route:r.route, method:r.method, public:r.public})-[:HANDLED_BY]->(f)", {"rows": rows})
    st.q("MATCH (r:Repo {name:$app}), (e:Endpoint) CREATE (r)-[:EXPOSES]->(e)", {"app": app})


def load_owners(st: Store, target: Target) -> None:
    if target.codeowners is None or not target.codeowners.exists():
        return
    rows = []
    for line in target.codeowners.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            path, team = line.split()[:2]
            rows.append({"file": path, "team": team})
    st.q("UNWIND $rows AS r MERGE (t:Team {name:r.team}) WITH t, r MATCH (m:Module {layer:'app', file:r.file}) "
         "MERGE (t)-[:OWNS]->(m)", {"rows": rows})


def load_advisories(st: Store, vulns: list[Vuln], pins: dict[str, str], cg: callgraph.CallGraph,
                    parsed: list[str], progress=print) -> None:
    for i, v in enumerate(vulns, 1):
        sym = {"symbols": [], "confidence": 0, "source": "unparsed", "rationale": "Package source is not analyzed",
               "patch_url": None}
        if v.package in parsed:
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


def full_ingest(st: Store, variant: str = "baseline", progress=print, pins: dict[str, str] | None = None,
                target: Target | None = None, parse: list[str] | None = None) -> dict:
    """Build every graph layer. `pins` overrides the requirements file (used to build a candidate-fix scratch graph);
    `parse` forces the set of dependency packages whose source is analysed (a candidate graph must analyse the same
    packages as the baseline, even ones that no longer have advisories)."""
    guard.require_writable("Rebuilding the graph", st)
    target = target or targets.get(None)
    app_dir = prepare_app_dir(variant, target)
    pins = pins or parse_requirements(target.requirements)
    direct = ([l.strip() for l in target.direct_file.read_text().split() if l.strip()]
              if target.direct_file and target.direct_file.exists() else list(pins))
    progress("querying OSV advisories")
    vulns = query_pinned(pins)
    progress("building call graph from app + dependency sources")
    cg, parsed = build_callgraph(pins, app_dir, target,
                                 [p for p in parse if p in pins] if parse else parsed_packages(target, pins, vulns), progress)
    st.reset()
    st.ensure_indexes()
    progress("loading supply-chain layer")
    load_supply_chain(st, pins, direct, target, parsed)
    progress("loading code layer")
    load_code(st, cg, pins, target)
    load_owners(st, target)
    load_advisories(st, vulns, pins, cg, parsed, progress)
    return {"functions": len(cg.funcs), "edges": len(cg.edges), "vulns": len(vulns), "parsed": parsed}


def reload_app_layer(st: Store, variant: str) -> dict:
    """What-if: re-ingest only the app's code layer from a variant, leaving dependencies and advisories alone."""
    guard.require_writable("The what-if re-ingest", st)
    target = targets.of_store(st)
    pins = parse_requirements(target.requirements)
    parsed = [r[0] for r in st.q("MATCH (p:PackageVersion {parsed:true}) RETURN p.name")]
    cg, _ = build_callgraph(pins, prepare_app_dir(variant, target), target, parsed)
    st.q("MATCH (n) WHERE (n:Function OR n:Module) AND n.layer = 'app' DETACH DELETE n")
    st.q("MATCH (e:Endpoint) DETACH DELETE e")
    load_code(st, cg, pins, target, only_app=True)
    load_owners(st, target)
    return {"functions": len(cg.funcs), "edges": len(cg.edges)}
