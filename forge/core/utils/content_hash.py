"""
forge.core.utils.content_hash — generic file-content hashing.

Generalizes the hashing pattern already used by
``forge.core.utils.port_signature`` (canonicalize → sha256) into a
reusable, source-agnostic building block: hashing arbitrary files by
content, not by modification time. This is the primitive that
``forge.core.provenance`` builds content-hash-based staleness detection on
top of (release-plan §12/§5.1: "Modification times may remain an
optimization, but must not be the source of truth").

Deliberately minimal: no YAML-aware canonicalization here (unlike
``port_signature``, which canonicalizes port lists) — these functions hash
raw file bytes. A caller that needs "same semantic content, different key
order" equivalence should canonicalize before hashing (e.g. re-serialize
parsed YAML with sorted keys), not rely on this module to do it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping


def hash_bytes(data: bytes) -> str:
    """Full sha256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def hash_file(path: "str | Path") -> str:
    """Full sha256 hex digest of the file at *path*'s content.

    Raises FileNotFoundError if *path* does not exist — callers that want
    to treat a missing file as "no hash" should check existence first.
    """
    return hash_bytes(Path(path).read_bytes())


def hash_files(paths: Iterable["str | Path"]) -> str:
    """A single deterministic hash over the content of multiple files.

    Order-independent: paths are sorted (by their string form) before
    hashing, so passing the same file set in a different order produces
    the same combined hash. Each file contributes its own path (relative
    string form, as given — callers should pass portable/relative paths if
    they want the combined hash to be portable) and content hash to the
    combined input, so a rename (same content, different path) *does*
    change the combined hash — this is deliberate, since a renamed source
    file is a real change to "what generated this".
    """
    entries = sorted(
        (str(p), hash_file(p)) for p in paths
    )
    combined_input = "\n".join(f"{path}:{digest}" for path, digest in entries)
    return hash_bytes(combined_input.encode("utf-8"))


def compute_preprocessing_hash(config: "Mapping[str, object]") -> str:
    """A deterministic hash over a dataset-materialization preprocessing
    configuration (release-plan Phase 10, slice 10.0A).

    *config* should carry only what actually determines the preprocessed
    output — adapter id/version, resize/rounding/saturation/tiling
    parameters, source-content hashes — never a local filesystem path,
    timestamp, or hostname; the caller is responsible for that exclusion,
    since this function has no way to distinguish a meaningful key from
    an environmental one.

    Canonicalized the same way ``forge.verify.dataset_format``'s
    ``compute_events_content_hash`` canonicalizes events (sorted-key,
    separator-normalized JSON) before hashing via :func:`hash_bytes` — no
    new hashing scheme, same "canonicalize, then hash" shape as
    :func:`hash_files` above, just over a mapping instead of a file list.
    """
    canonical = json.dumps(dict(config), sort_keys=True, separators=(",", ":"), default=str)
    return hash_bytes(canonical.encode("utf-8"))
