#!/usr/bin/env python3
"""vision_pipeline_demo's golden model — a real GoldenModelProvider
(release-plan Phase 10, slice 10.0A/10.1 Decision C).

Computes the exact expected output for each pixel event:

    normalized = clamp(scale*pixel + offset, 0, 255)
    threshold_mask = 1 if normalized >= threshold else 0

The identical closed-form formula ``pixel_normalizer.cpp`` implements in
HLS and ``threshold_rtl.v`` implements in RTL — this is the project-owned
algorithm; FORGE's :func:`forge.verify.golden_model.run_golden_model`
owns invocation determinism and hashing (never this module).
"""
from __future__ import annotations

from typing import Any, Mapping

from forge.verify.dataset_adapter import CanonicalDataset
from forge.verify.golden_model import (
    EXPECTED_DATASET_SCHEMA,
    ExpectedDataset,
    register_golden_model_provider,
)

PROVIDER_ID = "vision_pipeline.quickstart_normalizer_threshold"
PROVIDER_VERSION = "1.0"

# Must match algo/normalizer/pixel_normalizer.h's compile-time constants
# and algo/rtl/threshold_rtl.v's THRESHOLD parameter exactly.
DEFAULT_SCALE_NUM = 3
DEFAULT_SCALE_DEN = 2
DEFAULT_OFFSET = -64
DEFAULT_THRESHOLD = 96


class QuickstartNormalizerThresholdProvider:
    provider_id = PROVIDER_ID
    provider_version = PROVIDER_VERSION

    def evaluate(
        self,
        dataset: CanonicalDataset,
        config: "Mapping[str, object]",
    ) -> ExpectedDataset:
        scale_num = config.get("scale_num", DEFAULT_SCALE_NUM)
        scale_den = config.get("scale_den", DEFAULT_SCALE_DEN)
        offset = config.get("offset", DEFAULT_OFFSET)
        threshold = config.get("threshold", DEFAULT_THRESHOLD)

        events: "list[dict[str, Any]]" = []
        for ev in dataset.events:
            pixel = int(ev["in"]["pixel"], 0)
            scaled = (pixel * scale_num) // scale_den + offset
            normalized = max(0, min(255, scaled))
            mask = 1 if normalized >= threshold else 0
            events.append({
                "expected": {
                    "normalized_pixel": normalized,
                    "threshold_mask": mask,
                },
            })

        return ExpectedDataset(
            schema=EXPECTED_DATASET_SCHEMA,
            event_ids=list(dataset.metadata.event_ids),
            events=events,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
        )


PROVIDER = QuickstartNormalizerThresholdProvider()

register_golden_model_provider(PROVIDER.provider_id, PROVIDER)


# ── Slice 10.2: pixel-result path (adds gradient_magnitude) ──────────────

PIXEL_RESULT_PROVIDER_ID = "vision_pipeline.pixel_result"
PIXEL_RESULT_PROVIDER_VERSION = "1.0"


class PixelResultProvider:
    """Golden model for slice 10.2's pixel-result path: everything
    QuickstartNormalizerThresholdProvider computes, plus
    ``gradient_magnitude`` from the same Sobel kernel ``sobel_hls.cpp``
    implements (preflight.md §9.1, frozen):

        Gx = -r0c0 + r0c2 - 2*r1c0 + 2*r1c2 - r2c0 + r2c2
        Gy = -r0c0 - 2*r0c1 - r0c2 + r2c0 + 2*r2c1 + r2c2
        gradient_magnitude = clamp(abs(Gx) + abs(Gy), 0, 4095)

    Unlike the per-event, neighbor-blind quickstart provider, this one
    needs each pixel's 3x3 neighborhood -- so it first reconstructs each
    frame as a normalized-pixel grid from every event sharing a
    frame_id (using x/y as coordinates, exactly as window_builder_rtl's
    line buffers do), zero-pads it (preflight.md §9.1's frozen border
    policy), then re-walks the dataset in its original event order.
    """

    provider_id = PIXEL_RESULT_PROVIDER_ID
    provider_version = PIXEL_RESULT_PROVIDER_VERSION

    def evaluate(
        self,
        dataset: CanonicalDataset,
        config: "Mapping[str, object]",
    ) -> ExpectedDataset:
        scale_num = config.get("scale_num", DEFAULT_SCALE_NUM)
        scale_den = config.get("scale_den", DEFAULT_SCALE_DEN)
        offset = config.get("offset", DEFAULT_OFFSET)
        threshold = config.get("threshold", DEFAULT_THRESHOLD)

        def normalize(pixel: int) -> int:
            scaled = (pixel * scale_num) // scale_den + offset
            return max(0, min(255, scaled))

        # Pass 1: reconstruct each frame's normalized-pixel grid.
        frames: "dict[int, dict[tuple[int, int], int]]" = {}
        for ev in dataset.events:
            x = int(ev["in"]["x"], 0)
            y = int(ev["in"]["y"], 0)
            frame_id = int(ev["in"]["frame_id"], 0)
            pixel = int(ev["in"]["pixel"], 0)
            frames.setdefault(frame_id, {})[(x, y)] = normalize(pixel)

        def tap(frame: "dict[tuple[int, int], int]", x: int, y: int) -> int:
            return frame.get((x, y), 0)  # zero-padding, preflight.md §9.1

        # Pass 2: per-event, using the reconstructed grid for the window.
        events: "list[dict[str, Any]]" = []
        for ev in dataset.events:
            x = int(ev["in"]["x"], 0)
            y = int(ev["in"]["y"], 0)
            frame_id = int(ev["in"]["frame_id"], 0)
            pixel = int(ev["in"]["pixel"], 0)
            frame = frames[frame_id]

            normalized = normalize(pixel)
            mask = 1 if normalized >= threshold else 0

            r0c0, r0c1, r0c2 = tap(frame, x - 1, y - 1), tap(frame, x, y - 1), tap(frame, x + 1, y - 1)
            r1c0,        r1c2 = tap(frame, x - 1, y),                          tap(frame, x + 1, y)
            r2c0, r2c1, r2c2 = tap(frame, x - 1, y + 1), tap(frame, x, y + 1), tap(frame, x + 1, y + 1)

            gx = -r0c0 + r0c2 - 2 * r1c0 + 2 * r1c2 - r2c0 + r2c2
            gy = -r0c0 - 2 * r0c1 - r0c2 + r2c0 + 2 * r2c1 + r2c2
            magnitude = min(4095, abs(gx) + abs(gy))

            events.append({
                "expected": {
                    "normalized_pixel": normalized,
                    "threshold_mask": mask,
                    "gradient_magnitude": magnitude,
                },
            })

        return ExpectedDataset(
            schema=EXPECTED_DATASET_SCHEMA,
            event_ids=list(dataset.metadata.event_ids),
            events=events,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
        )


PIXEL_RESULT_PROVIDER = PixelResultProvider()

register_golden_model_provider(PIXEL_RESULT_PROVIDER.provider_id, PIXEL_RESULT_PROVIDER)
