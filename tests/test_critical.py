from faultline.critical import critical_path


def span(i, parent, start, dur, service="s"):
    return {"id": i, "parent": parent, "service": service, "op": i, "start_us": start, "duration_us": dur, "kind": "server", "error": False, "status": None}


def test_sequential_children_all_critical():
    spans = [span("root", None, 0, 100), span("a", "root", 0, 40), span("b", "root", 40, 50)]
    path, self_us = critical_path(spans)
    assert set(path) == {"root", "a", "b"}
    assert self_us == {"root": 10, "a": 40, "b": 50}


def test_parallel_children_only_latest_ending_is_critical():
    spans = [span("root", None, 0, 100), span("fast", "root", 10, 20), span("slow", "root", 10, 80)]
    path, self_us = critical_path(spans)
    assert set(path) == {"root", "slow"}
    assert self_us["root"] == 20 and self_us["slow"] == 80


def test_nested_path_and_overlap_skips_overlapping_sibling():
    # c1 ends at 90, c2 ends at 60 but overlaps c1 (starts 30 < 90 frontier... ends 60 <= frontier 30? no) -> skipped
    spans = [span("root", None, 0, 100), span("c1", "root", 30, 60), span("c2", "root", 20, 40), span("g", "c1", 40, 30)]
    path, self_us = critical_path(spans)
    assert set(path) == {"root", "c1", "g"}
    assert self_us["c1"] == 30


def test_missing_parent_treated_as_root():
    spans = [span("orphan", "ghost", 0, 10)]
    assert critical_path(spans)[0] == ["orphan"]
