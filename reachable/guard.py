"""Backend enforcement of read-only public mode (UI buttons are cosmetic; this is the real control)."""
from __future__ import annotations

from . import config


class ReadOnlyError(PermissionError):
    pass


def require_writable(what: str) -> None:
    if config.PUBLIC_MODE:
        raise ReadOnlyError(f"{what} is disabled in the read-only public demo")
