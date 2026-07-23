#!/usr/bin/env python3
"""Central supported flow/backend matrix — the product scope of forge.verify.

This module is the single authoritative source for:
  * Which (kind, backend) combinations are SUPPORTED for production use.
  * Which combinations are EXPERIMENTAL (not yet ready for handoff).
  * Which combinations are UNSUPPORTED (unrecognised).

Every command that processes flow declarations (generate, prepare, run, doctor)
must call ``validate_flow_matrix()`` before doing any further work.  This
prevents silent acceptance of unsupported or experimental combinations that
look superficially correct.

Public API
----------
  SUPPORTED_MATRIX         dict[kind → backend]   — canonical supported pairs
  EXPERIMENTAL_KINDS       dict[kind → reason]     — known but not ready
  FlowClassification       enum: SUPPORTED | EXPERIMENTAL | UNSUPPORTED
  classify_flow(kind, backend)               → FlowClassification
  validate_flow_matrix(kind, backend)        → list[str]  (empty = OK)
  canonical_backend_for(kind)               → str | None
"""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple


# ── Classification enum ────────────────────────────────────────────────────

class FlowClassification(Enum):
    """Classification of a (kind, backend) combination."""
    SUPPORTED    = "supported"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED  = "unsupported"


# ── Internal matrix entry ──────────────────────────────────────────────────

class _MatrixEntry(NamedTuple):
    canonical_backend: str
    classification:    FlowClassification
    reason:            str


# ── The canonical matrix — edit here to add/promote/demote flow kinds ─────

_MATRIX: dict[str, _MatrixEntry] = {
    # ── SUPPORTED ─────────────────────────────────────────────────────────
    "hls_csim": _MatrixEntry(
        canonical_backend="csim",
        classification=FlowClassification.SUPPORTED,
        reason="HLS C-simulation via compiled testbench binary",
    ),
    "single_module_rtl": _MatrixEntry(
        canonical_backend="xsim",
        classification=FlowClassification.SUPPORTED,
        reason="Single HLS-generated RTL module via Xilinx XSIM",
    ),
    "full_chip_rtl": _MatrixEntry(
        canonical_backend="xsim",
        classification=FlowClassification.SUPPORTED,
        reason="Full-chip RTL simulation via Xilinx XSIM (gen-top required)",
    ),
    # ── EXPERIMENTAL ──────────────────────────────────────────────────────
    "reduced_chain_rtl": _MatrixEntry(
        canonical_backend="xsim",
        classification=FlowClassification.EXPERIMENTAL,
        reason=(
            "Multi-module RTL chain simulation; TB generation and port "
            "extraction are not yet proven for this scope on the canonical path"
        ),
    ),
    # ── LEGACY alias ──────────────────────────────────────────────────────
    "hls_cosim": _MatrixEntry(
        canonical_backend="csim",
        classification=FlowClassification.EXPERIMENTAL,
        reason=(
            "Legacy alias for hls_csim; use 'hls_csim' in new designs"
        ),
    ),
}


# ── Convenience views ────────────────────────────────────────────────────

SUPPORTED_MATRIX: dict[str, str] = {
    k: v.canonical_backend
    for k, v in _MATRIX.items()
    if v.classification == FlowClassification.SUPPORTED
}
"""Mapping of fully-supported kind → canonical backend.

Only combinations in this dict are on the handoff-ready path.
"""

EXPERIMENTAL_KINDS: dict[str, str] = {
    k: v.reason
    for k, v in _MATRIX.items()
    if v.classification == FlowClassification.EXPERIMENTAL
}
"""Mapping of experimental kind → reason why it is not yet on the supported path."""


# ── Public API ─────────────────────────────────────────────────────────────

def classify_flow(kind: str, backend: str) -> FlowClassification:
    """Return the classification of a (kind, backend) pair.

    Returns ``UNSUPPORTED`` for any kind not in the matrix.

    Args:
        kind:    Flow kind string (e.g. ``"hls_csim"``).
        backend: Backend id string (e.g. ``"csim"``).

    Returns:
        ``FlowClassification`` enum value.
    """
    entry = _MATRIX.get(kind)
    if entry is None:
        return FlowClassification.UNSUPPORTED
    return entry.classification


def canonical_backend_for(kind: str) -> str | None:
    """Return the canonical backend for *kind*, or ``None`` if unknown.

    Args:
        kind: Flow kind string.

    Returns:
        Backend id string, or ``None`` for unrecognised kinds.
    """
    entry = _MATRIX.get(kind)
    return entry.canonical_backend if entry else None


def validate_flow_matrix(kind: str, backend: str) -> list[str]:
    """Validate that (kind, backend) is on the supported path.

    Returns a list of error strings.  An empty list means the combination
    is fully supported and no action is needed.

    Error cases:
      * Unrecognised kind → hard error with list of valid kinds.
      * Experimental kind → hard error with reason and migration hint.
      * Backend mismatch for a known kind → hard error with correction.

    Args:
        kind:    Flow kind string from ``design.verification.yml``.
        backend: Backend id string from ``design.verification.yml``.

    Returns:
        List of error strings; empty list means the combination is supported.
    """
    errors: list[str] = []

    entry = _MATRIX.get(kind)
    if entry is None:
        known = ", ".join(
            f"{k!r} ({v.canonical_backend})" for k, v in sorted(_MATRIX.items())
        )
        errors.append(
            f"Unknown flow kind {kind!r}.\n"
            f"  Supported: "
            + ", ".join(
                f"{k!r} → {v.canonical_backend!r}"
                for k, v in sorted(_MATRIX.items())
                if v.classification == FlowClassification.SUPPORTED
            )
            + f"\n  Experimental: "
            + ", ".join(sorted(EXPERIMENTAL_KINDS))
            + f"\n  All recognised kinds: {known}"
        )
        return errors

    if entry.classification == FlowClassification.EXPERIMENTAL:
        errors.append(
            f"Flow kind {kind!r} is EXPERIMENTAL and not on the supported path.\n"
            f"  Reason: {entry.reason}\n"
            f"  Supported kinds: "
            + ", ".join(
                f"{k!r} (backend: {v.canonical_backend!r})"
                for k, v in sorted(_MATRIX.items())
                if v.classification == FlowClassification.SUPPORTED
            )
            + f"\n  To use an experimental kind you must acknowledge the risk:\n"
            + f"  set flow field  experimental: true  in design.verification.yml"
        )
        return errors

    if backend != entry.canonical_backend:
        errors.append(
            f"Backend {backend!r} is not the canonical backend for kind {kind!r}.\n"
            f"  Expected: {entry.canonical_backend!r}\n"
            f"  The supported (kind → backend) matrix is:\n"
            + "".join(
                f"    {k!r} → {v.canonical_backend!r}\n"
                for k, v in sorted(_MATRIX.items())
                if v.classification == FlowClassification.SUPPORTED
            )
        )

    return errors
