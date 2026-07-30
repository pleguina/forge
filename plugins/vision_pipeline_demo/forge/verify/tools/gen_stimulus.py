#!/usr/bin/env python3
"""vision_pipeline_demo stimulus generator (release-plan Phase 10, slice
10.1).

Generates ``stimulus_current.svh`` for the ``quickstart_pipeline_xsim``
flow: drives one pixel event's full ``forge.pixel_stream.v1``-shaped
input, waits out the pipeline's total latency (3 HLS cycles +
2 RTL cycles = 5), then checks ``out_pixel``/``threshold_mask``/
``out_valid`` against a real, live-computed golden-model result — routed
through :class:`~forge.verify.dataset_service.DatasetService` and
:func:`~forge.verify.golden_model.run_golden_model` (release-plan Phase
10, slice 10.0A), never a hand-typed expected value.

Usage::

    python3 gen_stimulus.py --flow quickstart_pipeline_xsim [--event-id <n>]
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from forge.verify.dataset_adapter import DatasetSource
from forge.verify.dataset_service import DatasetService
from forge.verify.golden_model import run_golden_model, write_provider_provenance
from forge.verify.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

_DATASET_XML = Path(__file__).resolve().parents[1] / "schemas/data/vision_pipeline_quickstart_golden.xml"

_ADAPTER_ID = "vision_pipeline.pixel-stream-xml"
_PROVIDER_ID = "vision_pipeline.quickstart_normalizer_threshold"

# Total pipeline latency: 3 HLS (pixel_normalizer) + 2 RTL (threshold_rtl).
_TOTAL_LATENCY_CYCLES = 5


def _ensure_bootstrapped() -> None:
    """Register the plugin's dataset adapter and golden-model provider
    before touching their registries. `forge test run` bootstraps the
    plugin itself before calling into this module, but a standalone
    `python3 gen_stimulus.py` invocation (e.g. from
    `run_vision_pipeline_demo.sh`) does not go through that path, so
    this module must be able to bootstrap itself too."""
    import bootstrap as _bootstrap_mod
    _bootstrap_mod.bootstrap()


def _load_dataset_and_golden(dataset_path: Path):
    """Load the real golden dataset and run the real golden model over
    it once — both routed through the new Phase 10 slice 10.0A
    machinery, never a bypass."""
    _ensure_bootstrapped()
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=dataset_path))
    canonical = service.materialize(DatasetSource(serialized=serialized), _ADAPTER_ID, {})
    expected = run_golden_model(_PROVIDER_ID, canonical, {})
    return canonical, expected


def generate_for_flow(
    flow_name: str, event_id: int, out_path: Path, *, dataset_path: Path = _DATASET_XML,
) -> None:
    """Generate stimulus for *flow_name* event *event_id*.

    The framework guarantees that the testbench provides:
      - ``ap_clk``  — DUT clock (driven by TB)
      - ``ap_rst``  — synchronous active-high reset (driven by TB)

    Everything else is driven/checked here, read from the real dataset
    and the real golden model.
    """
    canonical, expected = _load_dataset_and_golden(dataset_path)
    key = str(event_id)
    if key not in canonical.metadata.event_ids:
        raise ValueError(
            f"Unknown event id: {event_id} "
            f"(known: {sorted(canonical.metadata.event_ids, key=int)}, dataset: {dataset_path})"
        )
    idx = canonical.metadata.event_ids.index(key)
    ev: "Dict[str, Any]" = canonical.events[idx]
    exp: "Dict[str, Any]" = expected.events[idx]["expected"]

    pixel        = int(ev["in"]["pixel"], 0)
    x            = int(ev["in"]["x"], 0)
    y            = int(ev["in"]["y"], 0)
    frame_id     = int(ev["in"]["frame_id"], 0)
    tile_id      = int(ev["in"]["tile_id"], 0)
    end_of_line  = int(ev["in"]["end_of_line"], 0)
    end_of_frame = int(ev["in"]["end_of_frame"], 0)
    pixel_valid  = int(ev["in"]["pixel_valid"], 0)

    normalized_pixel = exp["normalized_pixel"]
    threshold_mask   = exp["threshold_mask"]

    em = StimulusEmitter()

    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    with em.event_block(f"event_{event_id}"):
        # Top-level port names are instance-prefixed by `forge topgen
        # gen-top` ("norm_"/"thresh_" — from design.yml's instance
        # names `norm`/`thresh`), not the bare interface-contract role
        # names.
        em.drive("norm_pixel", pixel, width=8)
        em.drive("norm_x", x, width=12)
        em.drive("norm_y", y, width=12)
        em.drive("norm_frame_id", frame_id, width=16)
        em.drive("norm_tile_id", tile_id, width=16)
        em.drive("norm_end_of_line", end_of_line, width=1)
        em.drive("norm_end_of_frame", end_of_frame, width=1)
        em.drive("norm_pixel_valid", pixel_valid, width=1)
        # Wait out the pipeline's total latency (5 cycles) plus one extra
        # settle cycle, mirroring passthrough_demo's own
        # drive-then-wait-latency-plus-one convention.
        em.tick(cycles=_TOTAL_LATENCY_CYCLES + 1)

    with em.event_block("checks"):
        em.check("thresh_out_pixel", normalized_pixel, width=8, label="normalized_pixel_check", event_id=event_id)
        em.check("thresh_threshold_mask", threshold_mask, width=1, label="threshold_mask_check", event_id=event_id)
        em.check("thresh_out_valid", pixel_valid, width=1, label="out_valid_check", event_id=event_id)

    with em.event_block("drain"):
        em.drive("norm_pixel_valid", 0, width=1)
        em.tick(cycles=2)

    write_run_stimulus_svh(em, out_path, header_comment=f"flow={flow_name} event={event_id}")
    print(f"  wrote {out_path}")

    # release-plan §10.0C: persist the golden-model provider's identity
    # and hashes next to the stimulus it drove — otherwise this only
    # ever lived in this process's memory. Consumed later by
    # forge.verify.golden_comparison_result.build_golden_comparison_result
    # to join with a real simulation's per-event checks.
    provenance_path = out_path.parent / "golden_model_provenance.json"
    write_provider_provenance(expected, provenance_path)
    print(f"  wrote {provenance_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="vision_pipeline_demo stimulus generator")
    ap.add_argument("--flow",     required=True, help="Flow name from design.verification.yml")
    ap.add_argument("--event-id", dest="event_id", type=int, default=0)
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, args.event_id, out_path)


if __name__ == "__main__":
    main()
