#!/usr/bin/env python3
"""vision_pipeline_demo stimulus generator for the pixel-result-path flow.

Unlike ``gen_stimulus.py`` (quickstart tier: one isolated pixel per
invocation, full reset-to-drain per event), this flow streams an entire
frame's 64 pixels back-to-back through
``pixel_normalizer -> {window_builder_rtl -> sobel_hls, threshold_rtl +
12-cycle delay} -> edge_mask_merge_rtl`` and checks every merged output
in place, interleaved with driving later pixels -- exercising
window_builder_rtl's real end-of-frame drain and edge_mask_merge_rtl's
real exact-cycle merge, not just a single pixel in isolation.

Total latency from driving pixel k at norm's input to merge's
corresponding output is a *constant* 18 cycles for every k (verified
both by hand and by forge.analysis.latency_static.checker.check_merge_points
against this exact design -- see
forge/tests/test_latency_checker.py::test_vision_pipeline_demo_pixel_result_design_has_zero_merge_skew):

    norm(3) + window_builder_rtl(10) + sobel_hls(4) + merge(1) = 18
    norm(3) + threshold_rtl(2) + delay_cycles(12) + merge(1)    = 18

So pixel k's merge output is checked exactly 18 loop iterations after
pixel k was driven -- for k=0..63 driven at iterations 0..63, checks
happen at iterations 18..81 (overlapping the drive phase for iterations
18..63, then draining alone for iterations 64..81).

Usage::

    python3 gen_stimulus_pixel_result.py --flow pixel_result_xsim
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from forge.verification.dataset_adapter import DatasetSource
from forge.verification.dataset_service import DatasetService
from forge.verification.golden_model import run_golden_model, write_provider_provenance
from forge.verification.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

_DATASET_XML = Path(__file__).resolve().parents[1] / "schemas/data/vision_pipeline_quickstart_golden.xml"

_ADAPTER_ID = "vision_pipeline.pixel-stream-xml"
_PROVIDER_ID = "vision_pipeline.pixel_result"

# norm(3) + window_builder_rtl(10) + sobel_hls(4) + merge(1)
#         = norm(3) + threshold_rtl(2) + delay_cycles(12) + merge(1) = 18
#
# Empirically confirmed against a real xsim run (not just derived): pixel
# k's merge output is already correctly readable -- no race, no extra
# settle margin needed -- by the same loop iteration whose `.tick()`
# follows k+18 ticks after pixel k was driven. (An initial attempt added
# a defensive "+1" on the theory that checking the same edge a registered
# output updates on would race the DUT's own NBA update; the real run
# showed that reads one pixel too far ahead, so this codebase's
# `.tick()` + `.check()` pairing already settles cleanly with no margin.)
_PIXEL_TO_MERGE_LATENCY = 18
_N_PIXELS = 64


def _ensure_bootstrapped() -> None:
    import bootstrap as _bootstrap_mod
    _bootstrap_mod.bootstrap()


def _load_dataset_and_golden(dataset_path: Path):
    _ensure_bootstrapped()
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=dataset_path))
    canonical = service.materialize(DatasetSource(serialized=serialized), _ADAPTER_ID, {})
    expected = run_golden_model(_PROVIDER_ID, canonical, {})
    return canonical, expected


def generate_for_flow(
    flow_name: str, out_path: Path, *, dataset_path: Path = _DATASET_XML,
) -> None:
    canonical, expected = _load_dataset_and_golden(dataset_path)
    if len(canonical.events) != _N_PIXELS:
        raise ValueError(
            f"Expected exactly {_N_PIXELS} events (one 8x8 frame), got {len(canonical.events)}"
        )

    events: "list[Dict[str, Any]]" = list(canonical.events)
    expecteds: "list[Dict[str, Any]]" = [e["expected"] for e in expected.events]

    em = StimulusEmitter()

    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    total_iterations = (_N_PIXELS - 1) + _PIXEL_TO_MERGE_LATENCY + 1  # +1 margin
    for it in range(total_iterations):
        with em.event_block(f"cycle_{it}"):
            if it < _N_PIXELS:
                ev = events[it]["in"]
                em.drive("norm_pixel", int(ev["pixel"], 0), width=8)
                em.drive("norm_x", int(ev["x"], 0), width=12)
                em.drive("norm_y", int(ev["y"], 0), width=12)
                em.drive("norm_frame_id", int(ev["frame_id"], 0), width=16)
                em.drive("norm_tile_id", int(ev["tile_id"], 0), width=16)
                em.drive("norm_end_of_line", int(ev["end_of_line"], 0), width=1)
                em.drive("norm_end_of_frame", int(ev["end_of_frame"], 0), width=1)
                em.drive("norm_pixel_valid", int(ev["pixel_valid"], 0), width=1)
            else:
                em.drive("norm_pixel_valid", 0, width=1)

            em.tick()

            check_k = it - _PIXEL_TO_MERGE_LATENCY
            if 0 <= check_k < _N_PIXELS:
                exp = expecteds[check_k]
                ev_in = events[check_k]["in"]
                em.check(
                    "merge_normalized_pixel", exp["normalized_pixel"], width=8,
                    label="normalized_pixel_check", event_id=check_k,
                )
                em.check(
                    "merge_gradient_magnitude", exp["gradient_magnitude"], width=12,
                    label="gradient_magnitude_check", event_id=check_k,
                )
                em.check(
                    "merge_threshold_mask", exp["threshold_mask"], width=1,
                    label="threshold_mask_check", event_id=check_k,
                )
                em.check(
                    "merge_out_x", int(ev_in["x"], 0), width=12,
                    label="out_x_check", event_id=check_k,
                )
                em.check(
                    "merge_out_y", int(ev_in["y"], 0), width=12,
                    label="out_y_check", event_id=check_k,
                )
                em.check(
                    "merge_out_valid", 1, width=1,
                    label="out_valid_check", event_id=check_k,
                )
                em.check(
                    "merge_tag_mismatch", 0, width=1,
                    label="tag_mismatch_check", event_id=check_k,
                )

    write_run_stimulus_svh(em, out_path, header_comment=f"flow={flow_name} (streamed full frame)")
    print(f"  wrote {out_path}")

    provenance_path = out_path.parent / "golden_model_provenance.json"
    write_provider_provenance(expected, provenance_path)
    print(f"  wrote {provenance_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="vision_pipeline_demo pixel-result stimulus generator")
    ap.add_argument("--flow", required=True, help="Flow name from design.verification.yml")
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, out_path)


if __name__ == "__main__":
    main()
