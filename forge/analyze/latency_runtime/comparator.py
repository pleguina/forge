"""forge.analyze.latency_runtime.comparator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Compare HLS-predicted latency against simulation-observed latency and produce
a structured comparison result per module.
"""
from __future__ import annotations

import dataclasses
from typing import Optional


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compare(
    module_name: str,
    hls_predicted: Optional[int],
    observed: Optional[int],
) -> LatencyComparison:
    """Return a :class:`LatencyComparison` for a single module."""
    if hls_predicted is None or observed is None:
        return LatencyComparison(
            module_name=module_name,
            hls_predicted=hls_predicted,
            observed=observed,
            delta=None,
            verdict="unknown",
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
    )
