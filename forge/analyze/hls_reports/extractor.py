"""forge.analyze.hls_reports.extractor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Walk a build_hls/ tree and collect per-module synthesis reports.

Re-uses ``HLSMetricsExtractor`` from ``forge.hls.extract_hls_metrics`` so the
XML parsing logic lives in exactly one place.

Latency source precedence
-------------------------
For ``collect_reports`` to produce meaningful latency values the modules must
have been synthesised (stage ``synth`` or later).  If only a ``csim`` run
exists the csynth.xml will be absent and the module status will be "missing".
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ResourceCount:
    BRAM_18K: float = 0.0
    DSP: float = 0.0
    FF: float = 0.0
    LUT: float = 0.0
    URAM: float = 0.0


@dataclasses.dataclass
class ResourceUtil:
    used: ResourceCount = dataclasses.field(default_factory=ResourceCount)
    available: ResourceCount = dataclasses.field(default_factory=ResourceCount)
    utilization_pct: ResourceCount = dataclasses.field(default_factory=ResourceCount)


@dataclasses.dataclass
class HLSModuleReport:
    module_name: str
    solution: str                          # e.g. "solution1"
    status: str                            # "ok" | "error" | "missing"
    error: Optional[str]                   # set when status != "ok"

    # timing
    target_clock_ns: float = 0.0
    estimated_clock_ns: float = 0.0
    estimated_fmax_mhz: float = 0.0
    timing_met: Optional[bool] = None
    slack_ns: float = 0.0

    # latency / pipeline
    latency_best: int = 0
    latency_avg: int = 0
    latency_worst: int = 0
    pipeline_ii: int = 0
    pipeline_depth: int = 0
    pipeline_type: str = ""
    # csynth.xml's Interval-min/
    # Interval-max were already parsed by
    # forge.hls.extract_hls_metrics.HLSMetricsExtractor.extract_latency
    # but silently dropped before reaching this dataclass — recovered
    # here, same 0-default convention as the other latency fields above
    # (HLSMetricsExtractor.get_int already defaults to 0 when the XML
    # element is absent). A pipelined function's true throughput is a
    # range (min/max initiation interval), which pipeline_ii alone
    # (a single scalar) doesn't fully express.
    interval_min: int = 0
    interval_max: int = 0
    # buffering_capacity/occupancy/backpressure/
    # frame_rate — defined in the vocabulary (satisfies "track separately
    # where available") but genuinely reserved: csynth.xml (a synthesis-
    # time report) does not structurally carry any of this data — it is
    # runtime/simulation or RTL-generation-time information, not an HLS
    # synthesis fact. No current producer exists anywhere in this
    # codebase; these stay None rather than a fabricated value, same
    # "reserved, no effect yet" precedent as the width_adapter/
    # protocol_adapter/constant_source transformation kinds.
    buffering_capacity: Optional[int] = None
    occupancy: Optional[float] = None
    backpressure: Optional[bool] = None
    frame_rate: Optional[float] = None

    # resources
    resources: ResourceUtil = dataclasses.field(default_factory=ResourceUtil)

    # module metadata from XML
    top_module: str = ""
    part: str = ""


# ---------------------------------------------------------------------------
# Internal parser
# ---------------------------------------------------------------------------

def _parse_xml(xml_path: Path, module_name: str, solution: str) -> HLSModuleReport:
    from forge.hls.extract_hls_metrics import HLSMetricsExtractor

    extractor = HLSMetricsExtractor(xml_path)
    raw = extractor.extract_all()

    if raw["status"] != "success":
        return HLSModuleReport(
            module_name=module_name,
            solution=solution,
            status="error",
            error=raw.get("error", "unknown"),
        )

    t = raw["timing"]
    lat = raw["latency"]
    res = raw["resources"]
    info = raw["module_info"]

    used = ResourceCount(**res["used"])
    avail = ResourceCount(**res["available"])
    util = ResourceCount(**res["utilization_percent"])

    return HLSModuleReport(
        module_name=module_name,
        solution=solution,
        status="ok",
        error=None,
        target_clock_ns=t["target_clock_period_ns"],
        estimated_clock_ns=t["estimated_clock_period_ns"],
        estimated_fmax_mhz=t["estimated_frequency_mhz"],
        timing_met=t["timing_met"],
        slack_ns=t["slack_ns"],
        latency_best=lat["best_case_latency"],
        latency_avg=lat["average_case_latency"],
        latency_worst=lat["worst_case_latency"],
        pipeline_ii=lat["pipeline_ii"],
        pipeline_depth=lat["pipeline_depth"],
        pipeline_type=lat["pipeline_type"],
        interval_min=lat["interval_min"],
        interval_max=lat["interval_max"],
        resources=ResourceUtil(used=used, available=avail, utilization_pct=util),
        top_module=info["top_module"],
        part=info["part"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_reports(
    hls_build_root: Path,
    solution: str = "solution1",
) -> List[HLSModuleReport]:
    """Walk *hls_build_root* and return one :class:`HLSModuleReport` per module found.

    A module directory that has no ``csynth.xml`` (e.g. only ``csim`` was run)
    produces an entry with ``status="missing"`` rather than raising.
    """
    if not hls_build_root.exists():
        raise FileNotFoundError(f"HLS build root not found: {hls_build_root}")

    reports: List[HLSModuleReport] = []
    for mod_dir in sorted(hls_build_root.iterdir()):
        if not mod_dir.is_dir():
            continue
        xml_path = mod_dir / solution / "syn" / "report" / "csynth.xml"
        if xml_path.exists():
            reports.append(_parse_xml(xml_path, mod_dir.name, solution))
        else:
            reports.append(HLSModuleReport(
                module_name=mod_dir.name,
                solution=solution,
                status="missing",
                error=f"csynth.xml not found: {xml_path}",
            ))
    return reports


def latency_map_from_reports(reports: List[HLSModuleReport]) -> Dict[str, int]:
    """Return ``{module_name: worst_case_latency_cycles}`` for all OK reports."""
    return {
        r.module_name: r.latency_worst
        for r in reports
        if r.status == "ok" and r.latency_worst > 0
    }
