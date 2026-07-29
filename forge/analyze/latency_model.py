"""forge.analyze.latency_model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Shared, provenance-carrying latency representation (release-plan §4.1),
reused by every latency-related consumer: static analysis
(``forge.analyze.latency_static``), runtime observation
(``forge.analyze.latency_runtime``), and later slices' fixed/bounded/
elastic declarations (§4.2). Kept as its own module — not nested inside
``latency_static`` — so ``latency_runtime`` can depend on it without a
wrong-direction package dependency between analysis sibling packages.

Deliberately minimal: this module defines *data*, not analysis logic. It
has no dependency on ``forge.topgen``/``forge.ir``/``forge.verify`` so it
stays freely importable from any of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# The six latency-value sources release-plan §4.1 requires be
# distinguishable. Kept as a frozenset (not an Enum) to match this
# codebase's existing convention for small closed vocabularies validated
# at construction time (e.g. forge.topgen.ip.cardinality's KNOWN_* sets).
LATENCY_SOURCES = frozenset({
    "explicit_contract",        # a user-declared latency_cycles / latency: {...} block
    "hls_report",                # parsed HLS synthesis report (csynth.xml)
    "generated_transformation",  # FORGE-inserted RTL with a known, fixed depth (register_stages, delay_cycles, cdc_sync2ff)
    "inferred",                  # computed by FORGE's own analysis (e.g. a multi-hop path sum)
    "user_hint",                 # latency_hint — a rough, unverified manual estimate
    "runtime_observation",       # measured from a simulation probe
})

# The three timing kinds release-plan §4.2 requires. Defined here (not in
# forge.topgen.config) because both the schema-input side (§4.2) and the
# analysis-output side (this module, already used by §4.1) need the same
# vocabulary, and this module is the one with no upstream dependencies.
LATENCY_KINDS = frozenset({"fixed", "bounded", "elastic"})


class LatencyModelError(ValueError):
    """Raised when a LatencyProvenance/LatencyValue is constructed with
    data outside this module's declared vocabulary — a programming error
    in a caller, not a user-input validation failure (user-facing YAML
    validation happens in forge.topgen.validation, which raises/reports
    its own structured errors well before reaching this module)."""


@dataclass
class LatencyProvenance:
    """Where one latency value came from — release-plan §4.1's
    "each latency value must record its provenance" requirement."""
    source: str
    detail: Optional[str] = None

    def __post_init__(self) -> None:
        if self.source not in LATENCY_SOURCES:
            raise LatencyModelError(
                f"unknown latency source {self.source!r} — must be one of "
                f"{sorted(LATENCY_SOURCES)}"
            )


@dataclass
class LatencyValue:
    """A single latency figure, kind-tagged (§4.2) and provenance-tagged
    (§4.1). ``kind='fixed'`` is the only kind actually produced before
    Phase 4 slice 2 lands — ``bounded``/``elastic`` construction is
    already supported here so slice 2 doesn't need to touch this module.
    """
    kind: str = "fixed"
    cycles: Optional[int] = None
    min_cycles: Optional[int] = None
    max_cycles: Optional[int] = None
    provenance: Optional[LatencyProvenance] = None

    def __post_init__(self) -> None:
        if self.kind not in LATENCY_KINDS:
            raise LatencyModelError(
                f"unknown latency kind {self.kind!r} — must be one of {sorted(LATENCY_KINDS)}"
            )
