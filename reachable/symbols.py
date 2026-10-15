"""Map an advisory to the function(s) that contain the vulnerability.

1. Deterministic: download the fix commit, find which functions of the *vulnerable* release
   contain the removed / surrounding lines (line-number independent, matches on line text).
2. Gemini: given the advisory text, the diff and those candidates, pick the vulnerable symbol(s)
   and explain why (structured JSON output). Without an API key a transparent heuristic is used
   and labelled as such.

Results are cached in data/cache/symbol_map.json so the demo is reproducible and cheap.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from . import config, llm
from .advisories import Vuln
from .callgraph import CallGraph

PATCH_CACHE = config.CACHE / "patches"
PATCH_CACHE.mkdir(parents=True, exist_ok=True)
SYMBOL_MAP = config.CACHE / "symbol_map.json"
CURATED = config.ROOT / "data" / "curated_symbols.json"
SKIP_PATH = re.compile(r"(^|/)(tests?|docs?|examples?)/|CHANGES|\.rst$|\.md$|\.txt$")


@dataclass
class Hunk:
    path: str
    removed: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)


class Pick(BaseModel):
    symbols: list[str] = Field(description="Qualified names chosen from the candidate list, most important first")
    confidence: float = Field(description="0..1")
    rationale: str = Field(description="One or two sentences tying the diff to the vulnerability")


def fetch_patch(commit_url: str) -> str:
    sha = commit_url.rstrip("/").split("/")[-1]
    cache = PATCH_CACHE / f"{sha}.patch"
    if cache.exists():
        return cache.read_text()
    with urllib.request.urlopen(commit_url + ".patch", timeout=60) as r:
        text = r.read().decode("utf-8", errors="replace")
    cache.write_text(text)
    return text


def parse_patch(text: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    path, cur = None, None
    for line in text.splitlines():
        if line.startswith("diff --git"):
            path = line.split(" b/")[-1]
            cur = None
        elif line.startswith("@@") and path and path.endswith(".py") and not SKIP_PATH.search(path):
            cur = Hunk(path)
            hunks.append(cur)
        elif cur is not None:
            if line.startswith("-") and not line.startswith("---"):
                cur.removed.append(line[1:].strip())
            elif line.startswith("+") and not line.startswith("+++"):
                cur.added.append(line[1:].strip())
            elif line.startswith(" "):
                cur.context.append(line[1:].strip())
    return hunks


def candidates(cg: CallGraph, hunks: list[Hunk]) -> list[tuple[str, int]]:
    """Rank functions of the vulnerable release by how strongly the patch touches them."""
    scores: dict[str, int] = {}
    for h in hunks:
        funcs = [f for f in cg.funcs.values() if not f.is_module_body and h.path.endswith(f.file)]
        if not funcs:
            continue
        src = {f.qual: cg.func_source(f.qual) for f in funcs}
        for f in funcs:
            lines = {ln.strip() for ln in src[f.qual].splitlines()}
            hit = sum(3 for r in h.removed if len(r) > 6 and r in lines)
            hit += sum(1 for c in h.context if len(c) > 12 and c in lines)
            if hit:
                scores[f.qual] = scores.get(f.qual, 0) + hit
    # Removed registrations like `FullConstructor.add_multi_constructor(tag, FullConstructor.handler)`
    # live at module level: the handler functions they name are the real candidates.
    for h in hunks:
        in_function = {ln.strip() for f in cg.funcs.values() if not f.is_module_body and h.path.endswith(f.file)
                       for ln in cg.func_source(f.qual).splitlines()}
        text = " ".join(r for r in h.removed if r not in in_function)
        for cls_name, meth in re.findall(r"\b([A-Z]\w*)\.(\w+)\b", text):
            for c in cg.classes.values():
                if c.name == cls_name and h.path.endswith(cg.mods[c.module].file):
                    q = cg.find_method(c.qual, meth)
                    if q and not meth.startswith("add_"):
                        scores[q] = scores.get(q, 0) + 2
    return sorted(scores.items(), key=lambda kv: -kv[1])[:8]


def _load_map() -> dict:
    return json.loads(SYMBOL_MAP.read_text()) if SYMBOL_MAP.exists() else {}


def _save_map(m: dict) -> None:
    SYMBOL_MAP.write_text(json.dumps(m, indent=1, sort_keys=True))


def _ask_gemini(vuln: Vuln, diff_excerpt: str, cands: list[tuple[str, int]]) -> Pick:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=45000))
    names = [c for c, _ in cands]
    prompt = (
        "You are a vulnerability analyst. Decide which function(s) of the vulnerable library release "
        "contain the vulnerable behaviour described by the advisory, using the fix commit diff.\n"
        "Pick ONLY from the candidate list (copy the names exactly). Prefer the function whose logic was "
        "wrong, not helpers or tests. 1-2 symbols.\n\n"
        f"Advisory {vuln.id} ({vuln.package}): {vuln.summary}\n{vuln.details[:1500]}\n\n"
        f"Candidates (qualified name, change score): {cands}\n\nFix commit diff (truncated):\n{diff_excerpt[:6000]}"
    )
    resp = None
    for model in llm.models():
        try:
            resp = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Pick,
                                                   temperature=0))
            break
        except Exception as exc:
            if not llm.is_quota_error(exc):
                raise
            llm.mark_exhausted(model)
    if resp is None:
        raise RuntimeError("all Gemini models are out of quota")
    pick = Pick.model_validate_json(resp.text)
    pick.symbols = [s for s in pick.symbols if s in names]
    return pick


def extract_symbols(vuln: Vuln, cg: CallGraph, force: bool = False) -> dict:
    """Return {symbols, confidence, source, rationale, patch_url} for a vulnerability (cached)."""
    if CURATED.exists():
        manual = json.loads(CURATED.read_text()).get(vuln.id)
        if manual and all(q in cg.funcs for q in manual["symbols"]):
            return {"symbols": manual["symbols"], "confidence": manual["confidence"], "source": "curated",
                    "rationale": manual["rationale"], "patch_url": vuln.patch_urls[0] if vuln.patch_urls else None}
    cache = _load_map()
    cached = cache.get(vuln.id)
    usable = cached and all(q in cg.funcs for q in cached["symbols"])  # symbol must exist in this release
    if usable and not force and not (cached["source"] == "heuristic" and config.GEMINI_API_KEY):
        return cached
    result: dict = {"symbols": [], "confidence": 0.0, "source": "none", "rationale": "No fix commit in advisory",
                    "patch_url": None}
    for url in vuln.patch_urls:
        try:
            patch = fetch_patch(url)
        except Exception as exc:  # network / 404: try next commit
            result["rationale"] = f"patch fetch failed: {exc}"
            continue
        hunks = parse_patch(patch)
        cands = candidates(cg, hunks)
        if not cands:
            result["rationale"] = "Fix commit does not touch functions in the analyzed release"
            continue
        result["patch_url"] = url
        why = ""
        if config.GEMINI_API_KEY:
            for attempt in range(3):
                try:
                    pick = _ask_gemini(vuln, patch, cands)
                    if pick.symbols:
                        result.update(symbols=pick.symbols, confidence=round(pick.confidence, 2), source="gemini",
                                      rationale=pick.rationale)
                        break
                    why = "gemini returned no candidate; "
                    break
                except Exception as exc:
                    why = f"gemini failed ({exc.__class__.__name__}); "
                    time.sleep(2 * (attempt + 1))
            if result["source"] == "gemini":
                break
        top = cands[0]
        result.update(symbols=[top[0]], confidence=0.5, source="heuristic",
                      rationale=f"{why}highest-scoring changed function in the fix commit (score {top[1]}); not model-verified")
        break
    cache[vuln.id] = result
    _save_map(cache)
    return result
