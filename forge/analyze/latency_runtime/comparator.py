"""forge.analyze.latency_runtime.comparator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Compare HLS-predicted latency against simulation-observed latency and produce
a structured comparison result per module.

Release-plan §4.5 (Phase 4 slice 5) requires reports to show "predicted
latency; observed latency; discrepancy; source of each prediction;
affected path or interface." The first three were already here
(``hls_predicted``/``observed``/``delta``, kept exactly as-is for
back-compat — ``reporter.py`` reads them directly and needs no changes);
``predicted``/``observed`` (below, wrapping
:class:`~forge.analyze.latency_model.LatencyValue`) add the missing
provenance ("source of each prediction"), and ``path`` adds the missing
"affected path or interface."
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

from forge.analyze.latency_model import LatencyProvenance, LatencyValue


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LatencyComparison:
    module_name: str
    hls_predicted: Optional[int]   # worst-case latency from csynth.xml
    observed: Optional[int]        # measured from probe CSV
    delta: Optional[int]           # observed − predicted  (positive = slower than expected)
    verdict: str                   # "match" | "over" | "under" | "unknown"
    # release-plan §4.5: the same two latency figures above, wrapped with
    # provenance — additive, not a replacement (hls_predicted/observed
    # stay the source of truth for reporter.py's existing table).
    predicted: Optional[LatencyValue] = None
    observed_value: Optional[LatencyValue] = None
    # The affected path or interface this comparison covers, when the
    # caller has one available (e.g. from a LatencyGraph) — None when not
    # supplied, honest partial coverage rather than a mandatory field
    # that would force a fabricated value.
    path: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compare(
    module_name: str,
    hls_predicted: Optional[int],
    observed: Optional[int],
    *,
    predicted_source: str = "hls_report",
    path: Optional[List[str]] = None,
) -> LatencyComparison:
    """Return a :class:`LatencyComparison` for a single module.

    ``predicted_source``: the provenance to tag ``predicted`` with — the
    caller knows whether ``hls_predicted`` actually came from a parsed
    HLS report (the default) or, e.g., a module's explicit/hint latency
    declaration; this function only wraps the value, it doesn't infer
    where it came from.
    """
    predicted_value = (
        LatencyValue(cycles=hls_predicted, provenance=LatencyProvenance(predicted_source))
        if hls_predicted is not None else None
    )
    observed_value = (
        LatencyValue(cycles=observed, provenance=LatencyProvenance("runtime_observation"))
        if observed is not None else None
    )

    if hls_predicted is None or observed is None:
        return LatencyComparison(
            module_name=module_name,
            hls_predicted=hls_predicted,
            observed=observed,
            delta=None,
            verdict="unknown",
            predicted=predicted_value,
            observed_value=observed_value,
            path=path,
        )

    delta = observed - hls_predicted
    if delta == 0:
        verdict = "match"
    elif delta > 0:
        verdict = "over"
    else:
        verdict = "under"

    return LatencyComparison(
        module_name=module_name,
        hls_predicted=hls_predicted,
        observed=observed,
        delta=delta,
        verdict=verdict,
        predicted=predicted_value,
        observed_value=observed_value,
        path=path,
    )
