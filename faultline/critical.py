"""CRISP-style critical path over one trace's span tree (Zhang et al., USENIX ATC '22).

Walk from the root. At each span, visit children ordered by end time, latest first; a child is on the critical path
when it ends no later than the current frontier (the start of the previously chosen child, initially the parent's end).
Self time of a critical span = its duration minus the duration of its critical children.
"""
from __future__ import annotations


def critical_path(spans: list[dict]) -> tuple[list[str], dict[str, int]]:
    """Returns (ordered span ids on the critical path, {span id: critical self time us})."""
    by_id = {s["id"]: s for s in spans}
    children: dict[str | None, list[dict]] = {}
    for s in spans:
        parent = s["parent"] if s["parent"] in by_id else None
        children.setdefault(parent, []).append(s)
    roots = children.get(None, [])
    if not roots:
        return [], {}
    root = max(roots, key=lambda s: s["duration_us"])
    path: list[str] = []
    self_us: dict[str, int] = {}

    def end(s: dict) -> int:
        return s["start_us"] + s["duration_us"]

    def walk(span: dict) -> None:
        path.append(span["id"])
        frontier = end(span)
        chosen: list[dict] = []
        for c in sorted(children.get(span["id"], []), key=end, reverse=True):
            if end(c) <= frontier and c["start_us"] >= span["start_us"]:
                chosen.append(c)
                frontier = c["start_us"]
        self_us[span["id"]] = max(span["duration_us"] - sum(c["duration_us"] for c in chosen), 0)
        for c in chosen:  # latest-ending first keeps the path in reverse time order; order does not matter for sums
            walk(c)

    walk(root)
    return path, self_us
