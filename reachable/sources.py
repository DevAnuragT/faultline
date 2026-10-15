"""Download and unpack package sources from PyPI so they can be parsed statically.

Nothing is ever installed or executed: we only read the files with `ast`, which is why old
package versions work fine on any Python.
"""
from __future__ import annotations

import io
import json
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from .config import SRC_CACHE


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def fetch_package(name: str, version: str) -> Path:
    """Return a directory holding the unpacked distribution of name==version."""
    dest = SRC_CACHE / f"{_norm(name)}-{version}"
    if dest.exists() and any(dest.iterdir()):
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    meta = json.loads(_get(f"https://pypi.org/pypi/{name}/{version}/json"))
    files = meta["urls"]
    pick = next((f for f in files if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("none-any.whl")), None)
    pick = pick or next((f for f in files if f["packagetype"] == "sdist"), None)
    if pick is None:
        raise RuntimeError(f"no pure-python distribution for {name}=={version}")
    blob = _get(pick["url"])
    if pick["filename"].endswith((".whl", ".zip")):
        zipfile.ZipFile(io.BytesIO(blob)).extractall(dest)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tf:
            tf.extractall(dest, filter="data")
    return dest


def package_roots(dist_dir: Path, import_names: list[str] | None = None) -> list[Path]:
    """Find top-level importable package directories inside an unpacked distribution."""
    roots: list[Path] = []
    skip = {"tests", "test", "docs", "examples", "build", "ci", "scripts"}
    for init in sorted(dist_dir.rglob("__init__.py")):
        pkg = init.parent
        rel = pkg.relative_to(dist_dir).parts
        if any(p in skip or p.endswith((".dist-info", ".egg-info")) for p in rel):
            continue
        if any(pkg.is_relative_to(r) for r in roots):
            continue
        if import_names and pkg.name not in import_names:
            continue
        roots.append(pkg)
    # PyYAML ships Python 2 (lib/) and Python 3 (lib3/) trees: keep only the py3 one.
    if any("lib3" in r.parts for r in roots):
        roots = [r for r in roots if "lib3" in r.parts]
    return roots
