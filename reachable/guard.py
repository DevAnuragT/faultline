"""Backend enforcement of read-only public mode (UI buttons are cosmetic; this is the real control)."""
from __future__ import annotations

from . import config


class ReadOnlyError(PermissionError):
    pass


def require_writable(what: str, store=None) -> None:
    """Refuse mutations in public mode, except on an ephemeral store (a private scratch graph that is deleted
    afterwards and is never the shared graph visitors see)."""
    if config.PUBLIC_MODE and not getattr(store, "ephemeral", False):
        raise ReadOnlyError(f"{what} is disabled in the read-only public demo")
