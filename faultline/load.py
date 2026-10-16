"""Generate /dispatch traffic against HotROD, in parallel so its deliberate bottlenecks show up."""
from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config

CUSTOMERS = [123, 392, 731, 567]


def one(timeout: float = 35.0) -> dict:
    t = time.time()
    try:
        r = requests.get(f"{config.HOTROD_URL}/dispatch", params={"customer": random.choice(CUSTOMERS), "nonse": random.random()}, timeout=timeout)
        return {"status": r.status_code, "ms": (time.time() - t) * 1000}
    except Exception as exc:
        return {"status": exc.__class__.__name__, "ms": (time.time() - t) * 1000}


def run(n: int = 40, parallel: int = 4, progress=print) -> dict:
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        results = list(ex.map(lambda _: one(), range(n)))
    ok = [r for r in results if r["status"] == 200]
    ms = sorted(r["ms"] for r in results)
    out = {"requests": n, "ok": len(ok), "p50_ms": ms[len(ms) // 2], "max_ms": ms[-1]}
    progress(f"load: {out}")
    return out
