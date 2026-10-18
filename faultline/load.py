"""Generate traffic against the URLs the profile lists, in parallel so queueing and contention show up. No URLs = observe-only."""
from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import profile


def one(timeout: float | None = None) -> dict:
    t = time.time()
    timeout = timeout or float((profile.LOAD or {}).get("timeout_s", 35))
    url = random.choice(profile.LOAD_URLS)
    try:
        r = requests.get(url, params={"nonse": random.random()}, timeout=timeout)
        return {"status": r.status_code, "ms": (time.time() - t) * 1000}
    except Exception as exc:
        return {"status": exc.__class__.__name__, "ms": (time.time() - t) * 1000}


def run(n: int = 40, parallel: int = 4, progress=print) -> dict:
    if not profile.LOAD_URLS:
        progress("load: no urls in the profile (observe-only); ingest live traffic with --lookback instead")
        return {"requests": 0, "ok": 0, "p50_ms": 0.0, "max_ms": 0.0, "note": "observe-only"}
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        results = list(ex.map(lambda _: one(), range(n)))
    ok = [r for r in results if r["status"] == 200]
    ms = sorted(r["ms"] for r in results)
    out = {"requests": n, "ok": len(ok), "p50_ms": ms[len(ms) // 2], "max_ms": ms[-1]}
    progress(f"load: {out}")
    return out
