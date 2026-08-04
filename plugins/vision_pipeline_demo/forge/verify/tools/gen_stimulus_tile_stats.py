#!/usr/bin/env python3
"""vision_pipeline_demo stimulus generator for slice 10.3's
tile-statistics-path flow (release-plan Phase 10, preflight.md §6.2).

tile_stats_hls is real II=1 (release-plan Phase 10, slice 10.5
follow-up -- was II=2 at this flow's own original writing; see
modules.yml's tile_stats_hls entry and that module's own source header
for the real csynth-confirmed fix), so pixels are driven back-to-back,
same as gen_stimulus_pixel_result.py's own pixel-result path -- no
interleaved idle cycles needed.

One 8x8 frame (64 pixels) -> exactly one tile-statistics record, checked
once after the full frame has drained through
norm(3) + tstats(1) + tjoin(1) = 5 cycles from the last pixel's drive
iteration (63) -- so the check lands at iteration 68.

Usage::

    python3 gen_stimulus_tile_stats.py --flow tile_stats_xsim
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
_PROVIDER_ID = "vision_pipeline.tile_stats"

# norm(3) + tstats(1) + tjoin(1) = 5, measured from the drive iteration
# of the tile's *last* pixel (pixel 63, driven at iteration 63, since
# pixels are now back-to-back -- see module docstring).
_PIXEL_TO_JOIN_LATENCY = 5
_N_PIXELS = 64
_PIXEL_SPACING = 1  # tile_stats_hls is real II=1


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
    # Every entry is the same single tile-summary record at this slice's
    # one-tile-per-dataset scope (TileStatsProvider's own convention).
    tile_summary = expected.events[0]["expected"]

    em = StimulusEmitter()

    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    last_drive_iteration = (_N_PIXELS - 1) * _PIXEL_SPACING
    total_iterations = last_drive_iteration + _PIXEL_TO_JOIN_LATENCY + 1  # +1 margin

    for it in range(total_iterations):
        with em.event_block(f"cycle_{it}"):
            if it % _PIXEL_SPACING == 0 and (it // _PIXEL_SPACING) < _N_PIXELS:
                ev = events[it // _PIXEL_SPACING]["in"]
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

            if it == last_drive_iteration + _PIXEL_TO_JOIN_LATENCY:
                em.check(
                    "tjoin_out_minimum", tile_summary["minimum"], width=8,
                    label="tile_minimum_check", event_id=_N_PIXELS - 1,
                )
                em.check(
                    "tjoin_out_maximum", tile_summary["maximum"], width=8,
                    label="tile_maximum_check", event_id=_N_PIXELS - 1,
                )
                em.check(
                    "tjoin_out_mean", tile_summary["mean"], width=16,
                    label="tile_mean_check", event_id=_N_PIXELS - 1,
                )
                em.check(
                    "tjoin_out_variance", tile_summary["variance"], width=24,
                    label="tile_variance_check", event_id=_N_PIXELS - 1,
                )
                em.check(
                    "tjoin_out_tile_id", tile_summary["tile_id"], width=16,
                    label="tile_id_check", event_id=_N_PIXELS - 1,
                )
                em.check(
                    "tjoin_out_frame_id", tile_summary["frame_id"], width=16,
                    label="tile_frame_id_check", event_id=_N_PIXELS - 1,
                )
                em.check(
                    "tjoin_out_valid", 1, width=1,
                    label="tile_out_valid_check", event_id=_N_PIXELS - 1,
                )
                em.check(
                    "tjoin_join_mismatch", 0, width=1,
                    label="tile_join_mismatch_check", event_id=_N_PIXELS - 1,
                )

    write_run_stimulus_svh(em, out_path, header_comment=f"flow={flow_name} (streamed full frame, back-to-back)")
    print(f"  wrote {out_path}")

    provenance_path = out_path.parent / "golden_model_provenance.json"
    write_provider_provenance(expected, provenance_path)
    print(f"  wrote {provenance_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="vision_pipeline_demo tile-statistics stimulus generator")
    ap.add_argument("--flow", required=True, help="Flow name from design.verification.yml")
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, out_path)


if __name__ == "__main__":
    main()
