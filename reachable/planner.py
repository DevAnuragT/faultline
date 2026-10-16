"""Version-aware remediation planning.

The naive plan ("bump every exposed package to its fixed version") can be impossible: urllib3 2.x conflicts with
requests 2.25.1, which pins urllib3<1.27. The constraint lives on DEPENDS_ON edges in the graph, so the planner reads
it from there, rejects the conflicting bump with the reason, then searches for the smallest compatible co-upgrade.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from . import analysis
from .config import CACHE
from .graph import Store
from .ingest import _norm, parse_requires, pypi_requires

MAX_CANDIDATES = 60


@dataclass
class Plan:
    pins_before: dict[str, str]
    pins_after: dict[str, str]
    changes: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return not self.conflicts

    def as_dict(self) -> dict:
        return {"pins_before": self.pins_before, "pins_after": self.pins_after, "changes": self.changes,
                "rejected": self.rejected, "consistent": self.consistent, "conflicts": self.conflicts}


def releases(name: str) -> list[Version]:
    """Stable, non-yanked versions of a PyPI project, ascending (cached)."""
    cache = CACHE / "pypi" / f"{_norm(name)}-releases.json"
    if not cache.exists():
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=60) as r:
            data = json.load(r)
        ok = [v for v, files in data["releases"].items() if files and not all(f.get("yanked") for f in files)]
        cache.write_text(json.dumps(ok))
    out = []
    for v in json.loads(cache.read_text()):
        try:
            ver = Version(v)
        except InvalidVersion:
            continue
        if not ver.is_prerelease:
            out.append(ver)
    return sorted(out)


def current_pins(st: Store) -> dict[str, str]:
    return {r[0]: r[1] for r in st.q("MATCH (p:PackageVersion) RETURN p.name, p.version")}


def graph_constraints(st: Store) -> dict[tuple[str, str], dict[str, str]]:
    """Requirement specifiers of the INSTALLED packages, read from the DEPENDS_ON edges in the graph:
    {(dependent name, version): {normalized dependency name: specifier}}."""
    out: dict[tuple[str, str], dict[str, str]] = {}
    for a, av, b, spec in st.q("MATCH (a:PackageVersion)-[r:DEPENDS_ON]->(b:PackageVersion) "
                               "WHERE r.spec IS NOT NULL RETURN a.name, a.version, b.name, r.spec"):
        out.setdefault((a, av), {})[_norm(b)] = spec
    return out


def spec_on(dependent: str, version: str, dependency: str,
            installed: dict[tuple[str, str], dict[str, str]] | None = None) -> SpecifierSet | None:
    """What `dependent==version` requires of `dependency`. Installed packages are answered from the graph's DEPENDS_ON
    edges; candidate versions that are not in the graph yet come from PyPI release metadata."""
    if installed is not None and (dependent, version) in installed:
        spec = installed[(dependent, version)].get(_norm(dependency))
        return SpecifierSet(spec) if spec is not None else None
    for dep, spec in parse_requires(pypi_requires(dependent, version)):
        if dep == _norm(dependency):
            return SpecifierSet(spec)
    return None


def consistency_conflicts(pins: dict[str, str],
                          installed: dict[tuple[str, str], dict[str, str]] | None = None) -> list[str]:
    """Dry-run resolver: every pinned package's requirements on other pinned packages must hold."""
    out = []
    by_norm = {_norm(n): n for n in pins}
    for name, ver in pins.items():
        if installed is not None and (name, ver) in installed:
            reqs = list(installed[(name, ver)].items())
        else:
            reqs = parse_requires(pypi_requires(name, ver))
        for dep, spec in reqs:
            if dep in by_norm and not SpecifierSet(spec).contains(Version(pins[by_norm[dep]]), prereleases=True):
                out.append(f"{name} {ver} requires {by_norm[dep]}{spec}, but {by_norm[dep]}=={pins[by_norm[dep]]}")
    return out


def exposed_targets(st: Store, run_id: str) -> dict[str, dict]:
    return {u["package"]: u for u in analysis.upgrade_plan(st, run_id) if u["upgrade_to"]}


def plan_remediation(st: Store, run_id: str | None = None) -> Plan:
    run_id = run_id or analysis.latest_findings(st)[0]["run"]
    before = current_pins(st)
    inst = graph_constraints(st)
    targets = exposed_targets(st, run_id)
    after = dict(before)
    plan = Plan(before, after)
    reasons: dict[str, str] = {}
    for pkg, u in targets.items():
        after[pkg] = u["upgrade_to"]
        reasons[pkg] = f"clears {u['clears']} exposed advisor{'y' if u['clears'] == 1 else 'ies'}: " + ", ".join(u["advisories"])

    # Naive plan check: would each bump be possible on its own, with every other package left as installed?
    for pkg, u in targets.items():
        for dependent, dep_version in before.items():
            spec = spec_on(dependent, dep_version, pkg, inst)
            if spec is not None and not spec.contains(Version(u["upgrade_to"]), prereleases=True):
                plan.rejected.append({
                    "package": pkg, "version": u["upgrade_to"], "blocked_by": f"{dependent} {dep_version}",
                    "reason": f"{pkg}=={u['upgrade_to']} alone is impossible: {dependent} {dep_version} requires {pkg}{spec}",
                    "dependent": dependent})

    for _ in range(6):  # fixpoint: resolving one conflict can expose another
        changed = False
        for pkg in list(after):
            target = Version(after[pkg])
            for dependent, dep_version in list(after.items()):
                spec = spec_on(dependent, dep_version, pkg, inst)
                if spec is None or spec.contains(target, prereleases=True):
                    continue
                plan.rejected.append({
                    "package": pkg, "version": str(target), "blocked_by": f"{dependent} {dep_version}",
                    "reason": f"{dependent} {dep_version} requires {pkg}{spec}", "dependent": dependent})
                new = _compatible_dependent(dependent, dep_version, pkg, target,
                                            Version(targets[dependent]["upgrade_to"]) if dependent in targets else None)
                if new is None:
                    after[pkg] = before[pkg]
                    reasons[pkg] = f"kept at {before[pkg]}: no {dependent} release allows {pkg}=={target}"
                    plan.conflicts.append(reasons[pkg])
                else:
                    after[dependent] = str(new)
                    reasons.setdefault(dependent, f"co-upgrade: required to allow {pkg}=={target}")
                changed = True
                break
            if changed:
                break
        if not changed:
            break

    for rej in plan.rejected:
        dep = rej["dependent"]
        rej["resolution"] = (f"co-upgrade {dep} {before[dep]} -> {after[dep]}" if after[dep] != before[dep]
                             else "unresolved")
    for pkg, ver in after.items():
        if ver != before[pkg]:
            plan.changes.append({"package": pkg, "from": before[pkg], "to": ver, "reason": reasons.get(pkg, "")})
    plan.conflicts = [c for c in plan.conflicts if c] + consistency_conflicts(after, inst)
    plan.changes.sort(key=lambda c: c["package"].lower())
    return plan


def _compatible_dependent(dependent: str, current: str, dependency: str, wanted: Version,
                          floor: Version | None) -> Version | None:
    start = max(Version(current), floor or Version(current))
    scanned = 0
    for v in releases(dependent):
        if v < start or v == Version(current):
            continue
        scanned += 1
        if scanned > MAX_CANDIDATES:
            break
        spec = spec_on(dependent, str(v), dependency)
        if spec is None or spec.contains(wanted, prereleases=True):
            return v
    return None
