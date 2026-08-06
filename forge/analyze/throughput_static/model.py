"""forge.analyze.throughput_static.model — build
:class:`~forge.verify.throughput_result.StaticThroughputAnalysis` records
from real HLS synthesis reports.

No simulation is needed for any of this — every field is derived from a
module's already-parsed :class:`~forge.analyze.hls_reports.extractor.HLSModuleReport`
(``pipeline_ii``, ``estimated_fmax_mhz``) plus its declared data width
(sourced by the caller — e.g. from ``ip_info``/an interface contract —
not something this module resolves itself, since a "port width" isn't an
HLS-report fact).

This is a best-effort static estimate, the same "static analysis, not a
substitute for real timing closure" framing
``forge.analyze.latency_static`` already uses for latency — nominal
capacity assumes the reported ``fmax``/II hold in the actual generated
design, which real place-and-route may not achieve.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from forge.analyze.hls_reports.extractor import HLSModuleReport
from forge.verify.throughput_result import StaticThroughputAnalysis


def build_static_throughput_analysis(
    report: HLSModuleReport,
    *,
    data_width_bits: int,
    clock_frequency_mhz: "Optional[float]" = None,
) -> StaticThroughputAnalysis:
    """Build one module's static throughput estimate.

    ``clock_frequency_mhz`` defaults to the report's own
    ``estimated_fmax_mhz`` when not given explicitly (e.g. by a design's
    declared clock period, which may differ from what HLS estimated).
    ``pipeline_ii`` of 0 (unpopulated/not-pipelined) is treated as II=1
    (one record accepted per cycle) rather than raising a
    divide-by-zero — the common HLS convention for a fully-pipelined
    module.
    """
    ii = report.pipeline_ii if report.pipeline_ii > 0 else 1
    freq_mhz = clock_frequency_mhz if clock_frequency_mhz is not None else report.estimated_fmax_mhz
    records_per_cycle = 1.0 / ii
    nominal_capacity = freq_mhz * 1e6 * records_per_cycle
    return StaticThroughputAnalysis(
        module_name=report.module_name,
        clock_frequency_mhz=freq_mhz,
        pipeline_ii=ii,
        records_per_cycle=records_per_cycle,
        data_width_bits=data_width_bits,
        nominal_capacity_records_per_sec=nominal_capacity,
    )


def build_design_throughput_analysis(
    reports: "List[HLSModuleReport]",
    data_width_bits_by_module: "Dict[str, int]",
    *,
    clock_frequency_mhz_by_module: "Optional[Dict[str, float]]" = None,
) -> "Tuple[List[StaticThroughputAnalysis], Optional[str], Optional[float]]":
    """Build one :class:`StaticThroughputAnalysis` per ``status == "ok"``
    report, plus a design-level bottleneck: the module with the lowest
    nominal capacity in the chain, and its capacity as the
    design's predicted rate.

    Reports with ``status != "ok"`` (missing/error — e.g. csim-only
    builds with no ``csynth.xml``) are skipped, not fabricated with zero
    values. Modules absent from ``data_width_bits_by_module`` are also
    skipped — a static throughput estimate needs a real declared width,
    never a guessed one.

    Returns ``(analyses, bottleneck_module_name, predicted_rate)`` —
    the latter two are ``None`` when no module could be analyzed.
    """
    clock_map = clock_frequency_mhz_by_module or {}
    analyses: "List[StaticThroughputAnalysis]" = []
    for report in reports:
        if report.status != "ok":
            continue
        width = data_width_bits_by_module.get(report.module_name)
        if width is None:
            continue
        analyses.append(build_static_throughput_analysis(
            report, data_width_bits=width,
            clock_frequency_mhz=clock_map.get(report.module_name),
        ))

    if not analyses:
        return analyses, None, None

    bottleneck = min(analyses, key=lambda a: a.nominal_capacity_records_per_sec)
    return analyses, bottleneck.module_name, bottleneck.nominal_capacity_records_per_sec
