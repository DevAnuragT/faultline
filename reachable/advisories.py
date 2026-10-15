"""OSV advisory ingestion. Raw OSV JSON is cached in data/cache/osv so runs are reproducible offline."""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field

from packaging.version import InvalidVersion, Version

from .config import CACHE

OSV_CACHE = CACHE / "osv"
OSV_CACHE.mkdir(parents=True, exist_ok=True)


@dataclass
class Vuln:
    id: str
    aliases: list[str]
    package: str
    summary: str
    details: str
    severity: str
    fixed_version: str | None
    patch_urls: list[str] = field(default_factory=list)

    @property
    def cve(self) -> str | None:
        return next((a for a in self.aliases if a.startswith("CVE-")), None)


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _get_vuln(vid: str) -> dict:
    cache = OSV_CACHE / f"{vid}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    with urllib.request.urlopen(f"https://api.osv.dev/v1/vulns/{vid}", timeout=60) as r:
        data = json.load(r)
    cache.write_text(json.dumps(data, indent=1))
    return data


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _fixed_version(raw: dict, package: str, installed: str) -> str | None:
    try:
        cur = Version(installed)
    except InvalidVersion:
        return None
    best: Version | None = None
    for aff in raw.get("affected", []):
        if _norm(aff.get("package", {}).get("name", "")) != _norm(package):
            continue
        for rng in aff.get("ranges", []):
            if rng.get("type") != "ECOSYSTEM":
                continue
            for ev in rng.get("events", []):
                if "fixed" in ev:
                    try:
                        v = Version(ev["fixed"])
                    except InvalidVersion:
                        continue
                    if v > cur and (best is None or v < best):
                        best = v
    return str(best) if best else None


def _severity(raw: dict) -> str:
    sev = (raw.get("database_specific") or {}).get("severity")
    return (sev or "UNKNOWN").upper()


def query_pinned(pins: dict[str, str], pin_cache: bool = True) -> list[Vuln]:
    """pins: {package: version}. Returns de-duplicated vulnerabilities (GHSA preferred over PYSEC)."""
    cache = OSV_CACHE / "_query.json"
    key = json.dumps(sorted(pins.items()))
    if pin_cache and cache.exists() and json.loads(cache.read_text()).get("key") == key:
        hits = json.loads(cache.read_text())["hits"]
    else:
        names = list(pins)
        res = _post("https://api.osv.dev/v1/querybatch", {"queries": [
            {"package": {"name": n, "ecosystem": "PyPI"}, "version": pins[n]} for n in names]})
        hits = {n: [v["id"] for v in r.get("vulns", [])] for n, r in zip(names, res["results"])}
        cache.write_text(json.dumps({"key": key, "hits": hits}))

    out: dict[str, Vuln] = {}
    alias_to_id: dict[str, str] = {}
    for pkg, ids in hits.items():
        for vid in sorted(ids, key=lambda i: (not i.startswith("GHSA"), i)):
            raw = _get_vuln(vid)
            keys = {vid, *raw.get("aliases", [])}
            dup = next((alias_to_id[k] for k in keys if k in alias_to_id), None)
            if dup:
                continue
            patch = [r["url"] for r in raw.get("references", []) if "/commit/" in r["url"]]
            out[vid] = Vuln(vid, [a for a in raw.get("aliases", [])], pkg, raw.get("summary", ""),
                            raw.get("details", ""), _severity(raw), _fixed_version(raw, pkg, pins[pkg]),
                            [u.split("#")[0] for u in patch])
            for k in keys:
                alias_to_id[k] = vid
    return sorted(out.values(), key=lambda v: (v.package.lower(), v.id))
