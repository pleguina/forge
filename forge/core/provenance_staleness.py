"""
forge.core.provenance_staleness — content-hash confirmation for the
existing mtime-based staleness checkers ("Modification times may remain
an optimization, but must not be the source of truth").

``forge/core/stale_detection.py`` (``gen-top``'s own staleness check) and
``forge/verification/stale_artifact.py`` (verify-flow artifact staleness) both
compare source/artifact modification times only. This module adds an
optional *confirmation* step on top, without replacing either checker's
own logic or call sites: when a ``provenance.json`` (written by
``gen-top``, see ``forge/ir/provenance.py``) is
available next to a source file mtime flagged as newer, and that
manifest already recorded a content hash for the exact same file, compare
it against a freshly computed one. A touched-but-unchanged source no
longer reports as stale; a genuinely changed one still does, with or
without this confirmation.

Deliberately one-directional: this can only turn a *stale* mtime verdict
into *fresh* (a confirmed-unchanged content match), never the reverse — a
missing/stale manifest, or one that doesn't cover the file in question,
always falls back to the mtime verdict exactly as it was before this
slice (backward compatible; never a hard requirement on any project that
hasn't regenerated since this feature landed).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def confirms_fresh(source_path: "str | Path", provenance_dir: "str | Path") -> Optional[bool]:
    """Best-effort content-hash confirmation for *source_path*.

    Looks for ``provenance_dir/provenance.json`` and, if present, for a
    recorded hash of *source_path* in either its ``source_hashes`` (the
    common case — the file was an *input* to whatever wrote the manifest)
    or its ``output_hashes`` (e.g. checking a DUT RTL file that was
    itself one of `gen-top`'s generated outputs). The lookup key is
    ``source_hashes``/``output_hashes``'s own relative-path convention
    (relative to the manifest's own design directory, see
    ``forge/ir/provenance.py``'s ``_relative_key``) — tried both relative
    to *provenance_dir* (correct whenever the manifest's design directory
    *is* *provenance_dir*, the common default-output-location case) and
    as a bare filename (a looser fallback for a manifest built with a
    different design directory but the same file basename).

    Returns:
        ``True``  — a recorded hash exists and matches the file's current
                    content (mtime was misleading; the file is unchanged).
        ``False`` — a recorded hash exists and no longer matches (a real
                    content change, mtime or not).
        ``None``  — no usable provenance data (no manifest, unreadable,
                    or the file isn't tracked in it) — callers should fall
                    back to mtime-only behavior.
    """
    provenance_path = Path(provenance_dir) / "provenance.json"
    if not provenance_path.exists():
        return None

    source = Path(source_path)
    if not source.exists():
        return None

    try:
        from forge.ir.provenance import read_provenance
        manifest = read_provenance(provenance_path)
    except Exception:
        return None

    candidate_keys = []
    try:
        candidate_keys.append(os.path.relpath(source, provenance_dir))
    except ValueError:
        pass
    candidate_keys.append(source.name)

    recorded = None
    for key in candidate_keys:
        if key in manifest.source_hashes:
            recorded = manifest.source_hashes[key]
            break
        if key in manifest.output_hashes:
            recorded = manifest.output_hashes[key]
            break

    if recorded is None:
        return None

    try:
        from forge.core.utils.content_hash import hash_file
        current = hash_file(source)
    except OSError:
        return None

    return current == recorded


def describe_staleness_basis(content_confirmed_fresh: Optional[bool]) -> str:
    """A short, human-readable clause describing *why* a stale verdict is
    trusted — surfacing a real reason
    string, not just stale: yes/no, reusing
    ``forge.ir.provenance.explain_staleness``'s reason-formatting
    convention (a short declarative clause, no trailing punctuation) so
    the IR-level (`forge inspect --explain-staleness`) and artifact-level
    (`forge topgen validate --check-stale`/`forge verify`) staleness
    surfaces read consistently.
    """
    if content_confirmed_fresh is False:
        return "confirmed via content hash: source content changed"
    return "mtime-only — no provenance manifest available to confirm"
