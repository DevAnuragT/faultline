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

from . import profile

SOURCE_ROOT = profile.SOURCE_ROOT
RAW_BASE = profile.RAW_BASE
CACHE = config.ROOT / "data" / "cache" / "source"


def fetch_file(path: str) -> str:
    if SOURCE_ROOT:
        local = Path(SOURCE_ROOT) / path
        if local.exists():
            return local.read_text()
    cached = CACHE / path
    if cached.exists():
        return cached.read_text()
    if not RAW_BASE:
        raise FileNotFoundError(f"{path}: set code.source_root (a local checkout) or code.raw_base in the profile")
    with urllib.request.urlopen(RAW_BASE + path, timeout=20) as r:
        text = r.read().decode()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return text


def extract_function(text: str, function: str, path: str = "") -> dict | None:
    """Find a function by name in Go, Python, JavaScript/TypeScript, Java, Kotlin, C# or Rust source and return its body.
    `function` may be `Type.name` or `name`. Heuristic, not a parser: good enough to quote the right lines."""
    typ, _, name = function.rpartition(".")
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "go"
    lines = text.splitlines()
    if ext == "py":
        pat = re.compile(r"^([ \t]*)(?:async[ \t]+)?def[ \t]+%s[ \t]*\(" % re.escape(name), re.M)
        m = pat.search(text)
        if not m:
            return None
        start = text[:m.start()].count("\n"); indent = len(m.group(1))
        end = start
        for i in range(start + 1, len(lines)):
            l = lines[i]
            if l.strip() and (len(l) - len(l.lstrip())) <= indent and not l.lstrip().startswith(("#", "@")):
                break
            if l.strip():
                end = i
    else:
        if ext == "go":
            pat = (re.compile(r"^func \(\w+ \*?%s\) %s\(" % (re.escape(typ), re.escape(name)), re.M) if typ
                   else re.compile(r"^func %s\(" % re.escape(name), re.M))
        else:  # brace languages: `name(` at a declaration-ish position, not a call inside an expression
            pat = re.compile(r"^[ \t]*(?:[\w<>\[\],?.]+[ \t]+)*(?:function[ \t]+)?%s[ \t]*\([^;{]*\)[^;{]*\{" % re.escape(name), re.M)
        m = pat.search(text)
        if not m:
            return None
        start = text[:m.start()].count("\n")
        if ext == "go":
            end = next((i for i in range(start + 1, len(lines)) if lines[i] == "}"), len(lines) - 1)
        else:
            depth, end = 0, start
            for i in range(start, len(lines)):
                depth += lines[i].count("{") - lines[i].count("}")
                if depth <= 0 and i > start or (depth == 0 and "{" in lines[i] and "}" in lines[i] and i == start):
                    end = i; break
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
        found = extract_function(text, fn, path) if fn else None
        if not found and line:
            lines = text.splitlines(); i = max(int(line) - 1, 0)
            body = "\n".join(lines[max(0, i - 10):i + 20]); found = {"start_line": max(0, i - 10) + 1, "end_line": i + 20, "code": body, "sha": hashlib.sha256(body.encode()).hexdigest()[:16]}
        return {"file": path, "function": fn, "source": "span attributes", **(found or {"error": "function not found in file"})}
    try:
        text = fetch_file(ptr["file"])
    except Exception as exc:
        return {"error": f"cannot read {ptr['file']}: {exc.__class__.__name__}", "pointer": ptr}
    found = extract_function(text, ptr["function"], ptr["file"])
    if not found:
        return {"error": f"{ptr['function']} not found in {ptr['file']}", "pointer": ptr}
    url = f"{ptr['url']}#L{found['start_line']}-L{found['end_line']}" if ptr.get("url") else ""
    return {"file": ptr["file"], "function": ptr["function"], "why": ptr["why"], "url": url, "source": ptr["source"], **found}


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
