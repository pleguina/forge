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
  SUPPORTED_MATRIX         dict[kind → backend]   — default supported pairs
  EXPERIMENTAL_KINDS       dict[kind → reason]     — known but not ready
  FlowClassification       enum: SUPPORTED | EXPERIMENTAL | UNSUPPORTED
  MatrixEntry              dataclass: default_backend, allowed_backends, ...
  classify_flow(kind, backend)               → FlowClassification
  validate_flow_matrix(kind, backend)        → list[str]  (empty = OK)
  default_backend_for(kind)                 → str | None
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


# ── Classification enum ────────────────────────────────────────────────────

class FlowClassification(Enum):
    """Classification of a (kind, backend) combination."""
    SUPPORTED    = "supported"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED  = "unsupported"


# ── Matrix entry ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MatrixEntry:
    """One flow kind's position in the supported matrix.

    ``allowed_backends`` is always explicitly populated — never inferred
    from ``default_backend`` being absent — so there is no
    ``None``-means-something-special conditional to reason about at call
    sites.  ``default_backend`` is always a member of ``allowed_backends``
    and is what error messages recommend when a flow declares a backend
    outside the allowed set.
    """
    default_backend:  str
    allowed_backends: frozenset[str]
    classification:   FlowClassification
    reason:           str


def _single_backend_entry(
    default_backend: str,
    classification: FlowClassification,
    reason: str,
) -> MatrixEntry:
    """Construct a ``MatrixEntry`` whose only allowed backend is its default."""
    return MatrixEntry(
        default_backend=default_backend,
        allowed_backends=frozenset({default_backend}),
        classification=classification,
        reason=reason,
    )


# ── The canonical matrix — edit here to add/promote/demote flow kinds ─────

_MATRIX: dict[str, MatrixEntry] = {
    # ── SUPPORTED ─────────────────────────────────────────────────────────
    "hls_csim": _single_backend_entry(
        default_backend="csim",
        classification=FlowClassification.SUPPORTED,
        reason="HLS C-simulation via compiled testbench binary",
    ),
    "single_module_rtl": MatrixEntry(
        default_backend="xsim",
        allowed_backends=frozenset({"xsim", "verilator"}),
        classification=FlowClassification.SUPPORTED,
        reason="Single HLS-generated RTL module via Xilinx XSIM or Verilator",
    ),
    "full_chip_rtl": MatrixEntry(
        default_backend="xsim",
        allowed_backends=frozenset({"xsim", "verilator"}),
        classification=FlowClassification.SUPPORTED,
        reason="Full-chip RTL simulation via Xilinx XSIM or Verilator (gen-top required)",
    ),
    # ── EXPERIMENTAL ──────────────────────────────────────────────────────
    "reduced_chain_rtl": _single_backend_entry(
        default_backend="xsim",
        classification=FlowClassification.EXPERIMENTAL,
        reason=(
            "Multi-module RTL chain simulation; TB generation and port "
            "extraction are not yet proven for this scope on the canonical path"
        ),
    ),
    # ── LEGACY alias ──────────────────────────────────────────────────────
    "hls_cosim": _single_backend_entry(
        default_backend="csim",
        classification=FlowClassification.EXPERIMENTAL,
        reason=(
            "Legacy alias for hls_csim; use 'hls_csim' in new designs"
        ),
    ),
}


# ── Convenience views ────────────────────────────────────────────────────

SUPPORTED_MATRIX: dict[str, str] = {
    k: v.default_backend
    for k, v in _MATRIX.items()
    if v.classification == FlowClassification.SUPPORTED
}
"""Mapping of fully-supported kind → default backend.

Only combinations in this dict are on the handoff-ready path.
"""

EXPERIMENTAL_KINDS: dict[str, str] = {
    k: v.reason
    for k, v in _MATRIX.items()
    if v.classification == FlowClassification.EXPERIMENTAL
}
"""Mapping of experimental kind → reason why it is not yet on the supported path."""

MATRIX: "MappingProxyType[str, MatrixEntry]" = MappingProxyType(_MATRIX)
"""The complete, real support matrix — every recognised kind (SUPPORTED,
EXPERIMENTAL, and any future classification), each as a full ``MatrixEntry``
(``allowed_backends``/``reason`` included, unlike the lossy ``SUPPORTED_MATRIX``/
``EXPERIMENTAL_KINDS`` views above). A ``MappingProxyType`` — not just a type
annotation — so callers get a real runtime-immutable view over ``_MATRIX``,
not a mutable dict they could accidentally corrupt through the reference.
Used by ``forge.docsgen`` to generate ``docs/reference/support-matrix.md``."""


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


def default_backend_for(kind: str) -> str | None:
    """Return the default backend for *kind*, or ``None`` if unknown.

    Args:
        kind: Flow kind string.

    Returns:
        Backend id string, or ``None`` for unrecognised kinds.
    """
    entry = _MATRIX.get(kind)
    return entry.default_backend if entry else None


def allowed_backends_for(kind: str) -> frozenset[str]:
    """Return the full set of backends *kind* may declare, or an empty set.

    Args:
        kind: Flow kind string.

    Returns:
        Frozen set of backend id strings; empty for unrecognised kinds.
    """
    entry = _MATRIX.get(kind)
    return entry.allowed_backends if entry else frozenset()


def validate_flow_matrix(kind: str, backend: str) -> list[str]:
    """Validate that (kind, backend) is on the supported path.

    Returns a list of error strings.  An empty list means the combination
    is fully supported and no action is needed.

    Error cases:
      * Unrecognised kind → hard error with list of valid kinds.
      * Experimental kind → hard error with reason and migration hint.
      * Backend not in the kind's allowed set → hard error with correction.

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
            f"{k!r} ({v.default_backend})" for k, v in sorted(_MATRIX.items())
        )
        errors.append(
            f"Unknown flow kind {kind!r}.\n"
            f"  Supported: "
            + ", ".join(
                f"{k!r} → {v.default_backend!r}"
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
                f"{k!r} (backend: {v.default_backend!r})"
                for k, v in sorted(_MATRIX.items())
                if v.classification == FlowClassification.SUPPORTED
            )
            + f"\n  To use an experimental kind you must acknowledge the risk:\n"
            + f"  set flow field  experimental: true  in design.verification.yml"
        )
        return errors

    if backend not in entry.allowed_backends:
        errors.append(
            f"Backend {backend!r} is not allowed for kind {kind!r}.\n"
            f"  Allowed backends: {sorted(entry.allowed_backends)!r}\n"
            f"  Recommended (default): {entry.default_backend!r}\n"
            f"  The supported (kind → allowed backends) matrix is:\n"
            + "".join(
                f"    {k!r} → {sorted(v.allowed_backends)!r} (default: {v.default_backend!r})\n"
                for k, v in sorted(_MATRIX.items())
                if v.classification == FlowClassification.SUPPORTED
            )
        )

    return errors
