#!/usr/bin/env python3
"""``forge.throughput_result.v1`` — static + runtime throughput/backpressure
artifact (release-plan Phase 10, slice 10.0C — preflight.md §5 Decision B,
§8).

Two distinct result types, kept separate per Decision B rather than
folded into ``forge.analyze.hls_reports.extractor.HLSModuleReport``: a
synthesis-time (``csynth.xml``) report structurally cannot carry
simulation-derived occupancy/high-water data, so conflating the two would
misrepresent a static-analysis artifact as runtime data.

- :class:`StaticThroughputAnalysis` — module II, clock frequency,
  records/cycle, width, nominal capacity; derived from real
  :class:`~forge.analyze.hls_reports.extractor.HLSModuleReport`\\ s, no
  simulation needed. Built by :mod:`forge.analyze.throughput_static.model`.
- :class:`RuntimeThroughputResult` — accepted/emitted transactions, stall
  cycles, FIFO occupancy/high-water mark, full/empty events, measured
  throughput, dropped/duplicated transactions; derived from a real
  simulation's Tier 2 probe CSV. Built by
  :mod:`forge.analyze.throughput_runtime.probe`.

:class:`ThroughputResult` is the composite ``forge.throughput_result.v1``
artifact wrapping both, following the exact frozen-dataclass +
hand-written ``to_dict()`` shape ``FlowResult``/``ExpectedDataset``
already use (not ``ProvenanceManifest``'s ``to_dict``/``from_dict``
classmethod pair — nothing needs to reconstruct this artifact from JSON,
only render it, same as ``FlowResult``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forge.core.artifact_schema import ArtifactSchema

THROUGHPUT_RESULT_SCHEMA = ArtifactSchema("forge.throughput_result", "1.0")


@dataclass(frozen=True)
class StaticThroughputAnalysis:
    """One HLS module's static throughput estimate — no simulation
    needed, derived entirely from its synthesis report plus its declared
    clock frequency."""
    module_name:                       str
    clock_frequency_mhz:                float
    pipeline_ii:                        int
    records_per_cycle:                  float
    data_width_bits:                    int
    nominal_capacity_records_per_sec:   float

    def to_dict(self) -> "dict[str, Any]":
        return {
            "module_name": self.module_name,
            "clock_frequency_mhz": self.clock_frequency_mhz,
            "pipeline_ii": self.pipeline_ii,
            "records_per_cycle": self.records_per_cycle,
            "data_width_bits": self.data_width_bits,
            "nominal_capacity_records_per_sec": self.nominal_capacity_records_per_sec,
        }


@dataclass(frozen=True)
class RuntimeThroughputResult:
    """One FIFO instance's measured runtime behavior, derived from a real
    simulation's Tier 2 probe CSV (``full``/``empty``/``occupancy``/
    ``overflow_attempt``/``underflow_attempt`` columns)."""
    fifo_object_id:                 str
    accepted_transactions:           int
    emitted_transactions:            int
    stall_cycles:                    int
    high_water_mark:                 int
    full_events:                     int
    empty_events:                    int
    measured_rate_records_per_sec:   float
    dropped_transactions:            int
    duplicated_transactions:         int

    def to_dict(self) -> "dict[str, Any]":
        return {
            "fifo_object_id": self.fifo_object_id,
            "accepted_transactions": self.accepted_transactions,
            "emitted_transactions": self.emitted_transactions,
            "stall_cycles": self.stall_cycles,
            "high_water_mark": self.high_water_mark,
            "full_events": self.full_events,
            "empty_events": self.empty_events,
            "measured_rate_records_per_sec": self.measured_rate_records_per_sec,
            "dropped_transactions": self.dropped_transactions,
            "duplicated_transactions": self.duplicated_transactions,
        }


@dataclass(frozen=True)
class ThroughputResult:
    """The ``forge.throughput_result.v1`` artifact.

    ``design_hash`` is ``forge.ir.serialize.content_hash(project)`` —
    the same design-identity hash ``ProvenanceManifest``/``DesignGraph``
    already use, not a new one. ``dataset_hash``/``scenario_hash`` are
    optional: populated only when this result was produced against a
    concrete dataset/scenario (the static-only case, with no simulation
    run yet, leaves both ``None``).
    """
    schema:          ArtifactSchema
    design_hash:      str
    dataset_hash:    "str | None" = None
    scenario_hash:   "str | None" = None
    static:          "list[StaticThroughputAnalysis]" = field(default_factory=list)
    runtime:         "list[RuntimeThroughputResult]" = field(default_factory=list)
    predicted_rate:  "float | None" = None
    observed_rate:   "float | None" = None
    bottleneck:      "str | None" = None

    def to_dict(self) -> "dict[str, Any]":
        return {
            "schema": self.schema.to_dict(),
            "design_hash": self.design_hash,
            "dataset_hash": self.dataset_hash,
            "scenario_hash": self.scenario_hash,
            "static": [s.to_dict() for s in self.static],
            "runtime": [r.to_dict() for r in self.runtime],
            "predicted_rate": self.predicted_rate,
            "observed_rate": self.observed_rate,
            "bottleneck": self.bottleneck,
        }


def render_throughput_markdown(payload: "dict[str, Any]") -> str:
    """Render a ``ThroughputResult.to_dict()`` payload as Markdown for
    ``forge report`` — same schema-guard convention as
    ``forge.verify.results.render_results_markdown``."""
    schema = payload.get("schema") or {}
    if schema.get("name") != THROUGHPUT_RESULT_SCHEMA.name:
        return (
            "# Throughput\n\n"
            f"Unrecognised throughput schema: {schema.get('name')!r} "
            f"(expected {THROUGHPUT_RESULT_SCHEMA.name!r}) — cannot render.\n"
        )
    if schema.get("version") != THROUGHPUT_RESULT_SCHEMA.version:
        return (
            "# Throughput\n\n"
            f"Unsupported {THROUGHPUT_RESULT_SCHEMA.name} schema version: "
            f"{schema.get('version')!r} (this renderer understands "
            f"{THROUGHPUT_RESULT_SCHEMA.version!r}) — cannot render.\n"
        )

    lines = ["# Throughput", ""]
    if payload.get("bottleneck"):
        lines.append(f"- **bottleneck**: {payload['bottleneck']}")
    if payload.get("predicted_rate") is not None:
        lines.append(f"- **predicted rate**: {payload['predicted_rate']:,.0f} records/s")
    if payload.get("observed_rate") is not None:
        lines.append(f"- **observed rate**: {payload['observed_rate']:,.0f} records/s")
    lines.append("")

    static = payload.get("static") or []
    if static:
        lines += [
            "## Static analysis (HLS-report-derived)", "",
            "| Module | Clock (MHz) | II | Records/cycle | Width (bits) | Nominal capacity (records/s) |",
            "|---|---|---|---|---|---|",
        ]
        for s in static:
            lines.append(
                f"| {s['module_name']} | {s['clock_frequency_mhz']:.1f} | {s['pipeline_ii']} | "
                f"{s['records_per_cycle']:.3f} | {s['data_width_bits']} | "
                f"{s['nominal_capacity_records_per_sec']:,.0f} |"
            )
        lines.append("")

    runtime = payload.get("runtime") or []
    if runtime:
        lines += [
            "## Runtime (probe-derived)", "",
            "| FIFO | Accepted | Emitted | Stalls | High-water | Full events | Empty events | "
            "Measured rate (records/s) | Dropped | Duplicated |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in runtime:
            lines.append(
                f"| {r['fifo_object_id']} | {r['accepted_transactions']} | {r['emitted_transactions']} | "
                f"{r['stall_cycles']} | {r['high_water_mark']} | {r['full_events']} | {r['empty_events']} | "
                f"{r['measured_rate_records_per_sec']:,.0f} | {r['dropped_transactions']} | "
                f"{r['duplicated_transactions']} |"
            )
        lines.append("")

    if not static and not runtime:
        lines.append("No static or runtime throughput data available.\n")

    return "\n".join(lines)
