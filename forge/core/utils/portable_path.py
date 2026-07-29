"""A public, general-purpose policy for turning an absolute filesystem path
into a portable, non-absolute display string.

Extracted from ``forge.ir.provenance``'s private ``_relative_key()``
(release-plan Phase 8, slice 8.0A) so other consumers — the visual design
explorer's renderers, in particular — can reuse the same policy without
reaching into another module's underscore-prefixed implementation.
``forge.ir.provenance._relative_key()`` now delegates here instead of
keeping its own near-duplicate.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

from .content_hash import hash_file


def portable_display_path(path: "str | Path", roots: Sequence["str | Path"]) -> str:
    """Never returns an absolute path.

    Prefers a path relative to the first *root* it falls under (checked in
    the given order); for a path outside every given root, falls back to a
    basename + short content-hash label (e.g. ``"passthrough.v#a1b2c3d4"``)
    — never a ``'../'``-laden relative path, which could still reconstruct
    absolute directory structure one level at a time. A path that is
    already relative (and not absolute to begin with) passes through
    unchanged.
    """
    p = Path(path)
    if not p.is_absolute():
        return str(p)

    for root in roots:
        root_p = Path(root)
        try:
            return str(p.relative_to(root_p))
        except ValueError:
            continue

    try:
        digest = hash_file(p)[:8]
    except OSError:
        digest = hashlib.sha256(str(p).encode("utf-8")).hexdigest()[:8]
    return f"{p.name}#{digest}"
