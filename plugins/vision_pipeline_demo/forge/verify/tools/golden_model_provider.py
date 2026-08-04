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


# ── Slice 10.3: tile-statistics path ──────────────────────────────────────

TILE_STATS_PROVIDER_ID = "vision_pipeline.tile_stats"
TILE_STATS_PROVIDER_VERSION = "1.0"


class TileStatsProvider:
    """Golden model for slice 10.3's tile-statistics path
    (preflight.md §6.2/§9.1): one {minimum, maximum, mean, variance}
    record per tile, computed over the *normalized* pixel stream
    (tile_stats_hls's real input) -- the same normalize() formula
    QuickstartNormalizerThresholdProvider/PixelResultProvider already
    use.

    ``minimum``/``maximum`` are a plain running min/max; ``mean``/
    ``variance`` use the exact power-of-two-shift population formula
    frozen in preflight.md §9.1: ``mean = sum >> 6``,
    ``variance = max(0, (sum_of_squares >> 6) - mean**2)`` -- identical
    to plain integer floor-division by 64 for the non-negative values
    involved, so this reproduces tile_stats_hls's own arithmetic
    exactly, not just approximately.

    Only correct at this slice's single-tile-per-frame scope
    (dataset.events is exactly one 8x8 tile's 64 samples) -- multiple
    tiles per dataset is explicit future work alongside the
    concurrent-tile-accumulation RTL/HLS work itself (see
    tile_stats_hls.h's header).
    """

    provider_id = TILE_STATS_PROVIDER_ID
    provider_version = TILE_STATS_PROVIDER_VERSION

    def evaluate(
        self,
        dataset: CanonicalDataset,
        config: "Mapping[str, object]",
    ) -> ExpectedDataset:
        scale_num = config.get("scale_num", DEFAULT_SCALE_NUM)
        scale_den = config.get("scale_den", DEFAULT_SCALE_DEN)
        offset = config.get("offset", DEFAULT_OFFSET)

        def normalize(pixel: int) -> int:
            scaled = (pixel * scale_num) // scale_den + offset
            return max(0, min(255, scaled))

        normalized: "list[int]" = []
        tile_id = frame_id = 0
        for ev in dataset.events:
            pixel = int(ev["in"]["pixel"], 0)
            normalized.append(normalize(pixel))
            tile_id = int(ev["in"]["tile_id"], 0)
            frame_id = int(ev["in"]["frame_id"], 0)

        n = len(normalized)
        total = sum(normalized)
        total_sq = sum(v * v for v in normalized)
        mean = total // n
        variance = max(0, (total_sq // n) - mean * mean)

        expected_record = {
            "tile_id": tile_id,
            "frame_id": frame_id,
            "minimum": min(normalized),
            "maximum": max(normalized),
            "mean": mean,
            "variance": variance,
        }
        # event_ids/events length mirrors CanonicalDataset (ExpectedDataset's
        # own convention, see golden_model.py) even though every entry here
        # is the same single tile-summary record -- there is only one real
        # value at this slice's one-tile-per-dataset scope.
        events: "list[dict[str, Any]]" = [{"expected": expected_record} for _ in dataset.events]

        return ExpectedDataset(
            schema=EXPECTED_DATASET_SCHEMA,
            event_ids=list(dataset.metadata.event_ids),
            events=events,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
        )


TILE_STATS_PROVIDER = TileStatsProvider()

register_golden_model_provider(TILE_STATS_PROVIDER.provider_id, TILE_STATS_PROVIDER)


# ── Slice 10.5: packetizer path ───────────────────────────────────────────

PACKETIZER_PROVIDER_ID = "vision_pipeline.packetizer"
PACKETIZER_PROVIDER_VERSION = "1.0"

RECORD_KIND_PIXEL_RESULT = 0
RECORD_KIND_TILE_STATISTICS = 1


def pack_pixel_result_record(
    *, normalized_pixel: int, gradient_magnitude: int, threshold_mask: int,
    x: int, y: int, frame_id: int, tile_id: int, end_of_line: int, end_of_frame: int,
) -> int:
    """Pack one pixel-result record into the frozen 128-bit layout
    (preflight.md §6.1, MSB-first) -- bit-for-bit the same field order/
    widths as pixel_result_packer_rtl.v's own concatenation.
    """
    value = RECORD_KIND_PIXEL_RESULT << 126
    value |= (normalized_pixel & 0xFF) << 118
    value |= (gradient_magnitude & 0xFFF) << 106
    value |= (threshold_mask & 0x1) << 105
    value |= (x & 0xFFF) << 93
    value |= (y & 0xFFF) << 81
    value |= (frame_id & 0xFFFF) << 65
    value |= (tile_id & 0xFFFF) << 49
    value |= (end_of_line & 0x1) << 48
    value |= (end_of_frame & 0x1) << 47
    return value


def pack_tile_statistics_record(
    *, tile_id: int, minimum: int, maximum: int, mean: int, variance: int, frame_id: int,
) -> int:
    """Pack one tile-statistics record into the frozen 128-bit layout
    (preflight.md §6.1, MSB-first) -- bit-for-bit the same field order/
    widths as tile_stats_packer_rtl.v's own concatenation.
    """
    value = RECORD_KIND_TILE_STATISTICS << 126
    value |= (tile_id & 0xFFFF) << 110
    value |= (minimum & 0xFF) << 102
    value |= (maximum & 0xFF) << 94
    value |= (mean & 0xFFFF) << 78
    value |= (variance & 0xFFFFFF) << 54
    value |= (frame_id & 0xFFFF) << 38
    return value


class PacketizerProvider:
    """Golden model for slice 10.5's packetizer path (preflight.md
    §6.1/§6.2/§9.1): reuses ``PixelResultProvider``/``TileStatsProvider``
    verbatim for the per-domain field math (this module owns only the
    128-bit packing, not a second copy of the Sobel/threshold/tile-stats
    arithmetic) and packs each into the frozen record layout so the xsim
    checker can compare decoded ``packet_data`` slots directly against
    these hex values, without needing to reproduce real, clock-phase-
    dependent CDC-crossing cycle timing (see design_packetizer.yml's own
    header and gen_stimulus_packetizer.py for why this design is checked
    by content -- every expected record observed exactly once, from
    whichever beat/slot it lands in -- rather than by exact cycle, the
    same "no golden-model exact-cycle timing" precedent slice 10.4's
    cdc_xsim flow already established for a CDC-crossing design).
    """

    provider_id = PACKETIZER_PROVIDER_ID
    provider_version = PACKETIZER_PROVIDER_VERSION

    def evaluate(
        self,
        dataset: CanonicalDataset,
        config: "Mapping[str, object]",
    ) -> ExpectedDataset:
        pixel_result = PIXEL_RESULT_PROVIDER.evaluate(dataset, config)
        tile_stats = TILE_STATS_PROVIDER.evaluate(dataset, config)
        tile_record_hex = pack_tile_statistics_record(
            tile_id=tile_stats.events[0]["expected"]["tile_id"],
            minimum=tile_stats.events[0]["expected"]["minimum"],
            maximum=tile_stats.events[0]["expected"]["maximum"],
            mean=tile_stats.events[0]["expected"]["mean"],
            variance=tile_stats.events[0]["expected"]["variance"],
            frame_id=tile_stats.events[0]["expected"]["frame_id"],
        )

        events: "list[dict[str, Any]]" = []
        for ev, pr in zip(dataset.events, pixel_result.events):
            x = int(ev["in"]["x"], 0)
            y = int(ev["in"]["y"], 0)
            frame_id = int(ev["in"]["frame_id"], 0)
            tile_id = int(ev["in"]["tile_id"], 0)
            end_of_line = int(ev["in"]["end_of_line"], 0)
            end_of_frame = int(ev["in"]["end_of_frame"], 0)

            pixel_record_hex = pack_pixel_result_record(
                normalized_pixel=pr["expected"]["normalized_pixel"],
                gradient_magnitude=pr["expected"]["gradient_magnitude"],
                threshold_mask=pr["expected"]["threshold_mask"],
                x=x, y=y, frame_id=frame_id, tile_id=tile_id,
                end_of_line=end_of_line, end_of_frame=end_of_frame,
            )
            events.append({
                "expected": {
                    "pixel_record_hex": pixel_record_hex,
                    "tile_record_hex": tile_record_hex,
                },
            })

        return ExpectedDataset(
            schema=EXPECTED_DATASET_SCHEMA,
            event_ids=list(dataset.metadata.event_ids),
            events=events,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
        )


PACKETIZER_PROVIDER = PacketizerProvider()

register_golden_model_provider(PACKETIZER_PROVIDER.provider_id, PACKETIZER_PROVIDER)
