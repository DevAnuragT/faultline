"""Gemini model selection with quota-aware fallback.

Free-tier models have small daily quotas, so calls walk a chain of models and remember which are exhausted.
"""
from __future__ import annotations

from . import config

_exhausted: set[str] = set()


def models() -> list[str]:
    chain = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    live = [m for m in chain if m not in _exhausted]
    return live or chain


def is_quota_error(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(exc)


def mark_exhausted(model: str) -> None:
    _exhausted.add(model)


def is_transient(exc: Exception) -> bool:
    """5xx from the API (overloaded / unavailable): worth one retry on the same model before giving up."""
    code = getattr(exc, "code", None)
    return (isinstance(code, int) and code >= 500) or exc.__class__.__name__ == "ServerError"
