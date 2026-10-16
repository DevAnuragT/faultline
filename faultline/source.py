"""Read-only access to the code behind an operation, so the agent quotes real lines instead of guessing.

Resolution order: a local checkout (`SOURCE_ROOT`), then the public repository the demo app comes from, cached on
disk. Only the function the graph pointed at is returned, never the whole tree."""
from __future__ import annotations

import hashlib
import os
import re
import urllib.request
from pathlib import Path

from . import code as codemap, config

SOURCE_ROOT = os.environ.get("SOURCE_ROOT", "")
RAW_BASE = os.environ.get("SOURCE_RAW_BASE", "https://raw.githubusercontent.com/jaegertracing/jaeger/main/examples/hotrod/")
CACHE = config.ROOT / "data" / "cache" / "source"


def fetch_file(path: str) -> str:
    if SOURCE_ROOT:
        local = Path(SOURCE_ROOT) / path
        if local.exists():
            return local.read_text()
    cached = CACHE / path
    if cached.exists():
        return cached.read_text()
    with urllib.request.urlopen(RAW_BASE + path, timeout=20) as r:
        text = r.read().decode()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return text


def extract_function(text: str, function: str) -> dict | None:
    """Go: `func (recv *Type) Name(` or `func Name(`; the body ends at the first line that is just `}`."""
    typ, _, name = function.rpartition(".")
    pat = (re.compile(r"^func \(\w+ \*?%s\) %s\(" % (re.escape(typ), re.escape(name)), re.M) if typ
           else re.compile(r"^func %s\(" % re.escape(name), re.M))
    m = pat.search(text)
    if not m:
        return None
    lines = text.splitlines()
    start = text[:m.start()].count("\n")
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == "}"), len(lines) - 1)
    body = "\n".join(lines[start:end + 1])
    return {"start_line": start + 1, "end_line": end + 1, "code": body, "sha": hashlib.sha256(body.encode()).hexdigest()[:16]}


def read(service: str, op: str, span_code: str = "") -> dict:
    """The function behind an operation: file, lines, code, and a hash of the code (approvals are bound to it)."""
    ptr = codemap.pointer(service, op, span_code)
    if not ptr:
        return {"error": f"no code pointer known for {service} {op}"}
    if "location" in ptr:  # from span attributes: path[:line] [function]
        parts = ptr["location"].split()
        path, _, line = parts[0].partition(":")
        fn = parts[1] if len(parts) > 1 else ""
        try:
            text = fetch_file(path)
        except Exception as exc:
            return {"error": f"cannot read {path}: {exc.__class__.__name__}", "pointer": ptr}
        found = extract_function(text, fn) if fn else None
        if not found and line:
            lines = text.splitlines(); i = max(int(line) - 1, 0)
            body = "\n".join(lines[max(0, i - 10):i + 20]); found = {"start_line": max(0, i - 10) + 1, "end_line": i + 20, "code": body, "sha": hashlib.sha256(body.encode()).hexdigest()[:16]}
        return {"file": path, "function": fn, "source": "span attributes", **(found or {"error": "function not found in file"})}
    try:
        text = fetch_file(ptr["file"])
    except Exception as exc:
        return {"error": f"cannot read {ptr['file']}: {exc.__class__.__name__}", "pointer": ptr}
    found = extract_function(text, ptr["function"])
    if not found:
        return {"error": f"{ptr['function']} not found in {ptr['file']}", "pointer": ptr}
    return {"file": ptr["file"], "function": ptr["function"], "why": ptr["why"], "url": f"{ptr['url']}#L{found['start_line']}-L{found['end_line']}", "source": ptr["source"], **found}


def check_patch(patch: str, code: str) -> list[str]:
    """A patch may only remove lines that exist in the function it claims to change. Returns the problems, if any."""
    problems = []
    removed = [l[1:] for l in patch.splitlines() if l.startswith("-") and not l.startswith("---")]
    if not removed and not any(l.startswith("+") and not l.startswith("+++") for l in patch.splitlines()):
        problems.append("the patch adds or removes nothing")
    have = {l.strip() for l in code.splitlines()}
    for l in removed:
        if l.strip() and l.strip() not in have:
            problems.append(f"removes a line that is not in the function: {l.strip()[:80]!r}")
    return problems
