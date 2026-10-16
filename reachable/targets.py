"""Analysis targets: the application whose dependency vulnerabilities we triage.

A target is just (app package directories, pinned requirements file). The default is the self-written demo app; a real
open-source repository can be vendored under `targets/` and analysed by the identical pipeline.
The active target of a graph is recorded on its `Repo` node, so verification and actions look it up from the graph
(no global state; Streamlit serves several sessions).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Target:
    name: str                       # Repo node name and the `dist` label of the app's own modules
    label: str
    root: Path                      # directory containing the package dirs
    packages: tuple[str, ...]       # package directories (relative to root) that make up the app
    requirements: Path              # fully pinned requirements (pip freeze style)
    repo_path: str                  # where that file lives in GITHUB_REPO (for draft PRs)
    graph: str
    codeowners: Path | None = None
    direct_file: Path | None = None
    parsed: tuple[str, ...] | None = None   # None = auto: every pinned package that has advisories
    variants: dict = field(default_factory=dict)
    source: str = ""


DEMO_VARIANTS = {
    "baseline": {"label": "Baseline vulnshop", "edits": []},
    "no_preview_fetch": {
        "label": "Remove the outbound HTTP call from /preview",
        "edits": [("preview.py", "    resp = requests.get(url, timeout=5)\n    return resp.text[:500]",
                   "    return f\"preview unavailable for {url}\"")],
    },
}

TARGETS: dict[str, Target] = {
    "vulnshop": Target(
        name="vulnshop", label="vulnshop (demo app written for this project)", root=config.DEMO_APP,
        packages=("vulnshop",), requirements=config.DEMO_APP / "requirements.txt",
        repo_path="demo_app/requirements.txt", graph=config.GRAPH_NAME, codeowners=config.DEMO_APP / "CODEOWNERS",
        direct_file=config.DEMO_APP / "requirements.in", parsed=("PyYAML", "requests", "urllib3", "Jinja2"),
        variants=DEMO_VARIANTS, source="local"),
    "microblog": Target(
        name="microblog", label="microblog v0.15 (miguelgrinberg/microblog, MIT)", root=config.ROOT / "targets" / "microblog",
        packages=("app",), requirements=config.ROOT / "targets" / "microblog" / "requirements.txt",
        repo_path="targets/microblog/requirements.txt", graph=f"{config.GRAPH_NAME}_microblog",
        variants={"baseline": {"label": "microblog v0.15 as published", "edits": []}},
        source="https://github.com/miguelgrinberg/microblog/tree/v0.15 (commit a1d4f28)"),
}

DEFAULT = "vulnshop"


def get(name: str | None) -> Target:
    return TARGETS[name or DEFAULT]


def of_store(st) -> Target:
    """The target a graph was built for (its Repo node), defaulting to the demo app."""
    try:
        rows = st.q("MATCH (r:Repo) RETURN r.name LIMIT 1")
    except Exception:
        rows = []
    return TARGETS.get(rows[0][0], TARGETS[DEFAULT]) if rows else TARGETS[DEFAULT]
