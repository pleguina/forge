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

    Slice 10.7A: generalized to any number of tiles per dataset, matching
    tile_stats_hls's own concurrent-tile-column extension
    (tile_stats_hls.h's MAX_TILE_COLS). Events are grouped by ``tile_id``
    (not assumed to be a single tile) and each tile's own finalized
    record is repeated across every event belonging to that tile -- the
    same "one real value, echoed at every index" convention the original
    single-tile version already used, just scoped per tile instead of
    per whole dataset. A caller wanting the real hardware emission order
    (one record per tile, in the order tile_stats_hls actually completes
    them) derives it directly from the dataset's own x/y fields -- see
    gen_stimulus_full_functional.py, which appends this event's own
    record whenever ``(x % TILE_WIDTH == TILE_WIDTH-1) and (y %
    TILE_HEIGHT == TILE_HEIGHT-1)`` (tile_stats_hls.h's own frozen
    completion test), needing no new provider API.
    """

    TILE_WIDTH = 8
    TILE_HEIGHT = 8

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

        # Pass 1: group every event's normalized value by (frame_id, tile_id)
        # -- NOT tile_id alone. tile_id only encodes position *within* a
        # frame (the frozen (y//8)*1024+(x//8) formula, preflight.md §9.1),
        # so two different frames of the same fixed 8x8-tile shape reuse
        # identical tile_id values (every single-tile 8x8 frame is
        # tile_id=0, regardless of frame_id) -- grouping by tile_id alone
        # silently conflates two different frames' own 64 samples into one
        # combined "tile" once a dataset genuinely has more than one frame
        # (slice 10.7B's platform-wrapper dataset is the first one that
        # does; every earlier single-frame dataset this provider was
        # exercised against had no way to expose this, tile_id already
        # being effectively unique there).
        tiles: "dict[tuple[int, int], dict[str, Any]]" = {}
        event_keys: "list[tuple[int, int]]" = []
        for ev in dataset.events:
            pixel = int(ev["in"]["pixel"], 0)
            tile_id = int(ev["in"]["tile_id"], 0)
            frame_id = int(ev["in"]["frame_id"], 0)
            key = (frame_id, tile_id)
            event_keys.append(key)
            bucket = tiles.setdefault(key, {"tile_id": tile_id, "frame_id": frame_id, "values": []})
            bucket["values"].append(normalize(pixel))

        # Pass 2: closed-form population stats per (frame_id, tile_id)
        # (preflight.md §9.1's frozen formula, unchanged from the
        # single-tile version).
        records: "dict[tuple[int, int], dict[str, Any]]" = {}
        for key, bucket in tiles.items():
            values = bucket["values"]
            n = len(values)
            total = sum(values)
            total_sq = sum(v * v for v in values)
            mean = total // n
            variance = max(0, (total_sq // n) - mean * mean)
            records[key] = {
                "tile_id": bucket["tile_id"],
                "frame_id": bucket["frame_id"],
                "minimum": min(values),
                "maximum": max(values),
                "mean": mean,
                "variance": variance,
            }

        # Pass 3: echo each event's own tile record at that event's index
        # (event_ids/events length mirrors CanonicalDataset, ExpectedDataset's
        # own convention -- see golden_model.py).
        events: "list[dict[str, Any]]" = [
            {"expected": records[key]} for key in event_keys
        ]

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

        events: "list[dict[str, Any]]" = []
        for ev, pr, ts in zip(dataset.events, pixel_result.events, tile_stats.events):
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
            # This event's own tile's record (slice 10.7A: may differ
            # across events when a dataset has more than one tile --
            # TileStatsProvider echoes each tile's finalized record at
            # every event belonging to that tile). The real hardware only
            # *emits* a tile-statistics record on that tile's own
            # completion event (x%TILE_WIDTH==TILE_WIDTH-1 and
            # y%TILE_HEIGHT==TILE_HEIGHT-1); callers building an ordered
            # expected-record list for a stimulus check (e.g.
            # gen_stimulus_full_functional.py) select on that same test
            # rather than taking every event's tile_record_hex.
            tile_record_hex = pack_tile_statistics_record(
                tile_id=ts["expected"]["tile_id"],
                minimum=ts["expected"]["minimum"],
                maximum=ts["expected"]["maximum"],
                mean=ts["expected"]["mean"],
                variance=ts["expected"]["variance"],
                frame_id=ts["expected"]["frame_id"],
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


# ── Slice 10.7B: platform-wrapper path (real runtime-configurable threshold) ──

PLATFORM_WRAPPER_PROVIDER_ID = "vision_pipeline.platform_wrapper"
PLATFORM_WRAPPER_PROVIDER_VERSION = "1.0"


class PlatformWrapperProvider:
    """Golden model for slice 10.7B's platform-wrapper path (preflight.md
    §26/§9.1): identical to PacketizerProvider except the threshold
    comparison is real per-frame, not the shared DEFAULT_THRESHOLD
    constant -- config carries ``frame_thresholds: {frame_id: threshold}``
    (default DEFAULT_THRESHOLD for any frame_id not listed), matching
    threshold_configurable_rtl.v's own frame-boundary-gated real
    threshold, sourced from a real cdc: {kind: mailbox_transfer}
    crossing rather than a compile-time parameter. Reuses
    PixelResultProvider/TileStatsProvider verbatim for the normalized
    pixel/gradient/tile-statistics math (unaffected by threshold) --
    this class owns only the per-frame threshold_mask override and the
    128-bit packing, the same "reuse, don't re-derive" precedent
    PacketizerProvider itself already established.
    """

    provider_id = PLATFORM_WRAPPER_PROVIDER_ID
    provider_version = PLATFORM_WRAPPER_PROVIDER_VERSION

    def evaluate(
        self,
        dataset: CanonicalDataset,
        config: "Mapping[str, object]",
    ) -> ExpectedDataset:
        frame_thresholds = config.get("frame_thresholds", {})

        pixel_result = PIXEL_RESULT_PROVIDER.evaluate(dataset, config)
        tile_stats = TILE_STATS_PROVIDER.evaluate(dataset, config)

        events: "list[dict[str, Any]]" = []
        for ev, pr, ts in zip(dataset.events, pixel_result.events, tile_stats.events):
            x = int(ev["in"]["x"], 0)
            y = int(ev["in"]["y"], 0)
            frame_id = int(ev["in"]["frame_id"], 0)
            tile_id = int(ev["in"]["tile_id"], 0)
            end_of_line = int(ev["in"]["end_of_line"], 0)
            end_of_frame = int(ev["in"]["end_of_frame"], 0)

            threshold = frame_thresholds.get(frame_id, DEFAULT_THRESHOLD)
            normalized_pixel = pr["expected"]["normalized_pixel"]
            mask = 1 if normalized_pixel >= threshold else 0

            pixel_record_hex = pack_pixel_result_record(
                normalized_pixel=normalized_pixel,
                gradient_magnitude=pr["expected"]["gradient_magnitude"],
                threshold_mask=mask,
                x=x, y=y, frame_id=frame_id, tile_id=tile_id,
                end_of_line=end_of_line, end_of_frame=end_of_frame,
            )
            tile_record_hex = pack_tile_statistics_record(
                tile_id=ts["expected"]["tile_id"],
                minimum=ts["expected"]["minimum"],
                maximum=ts["expected"]["maximum"],
                mean=ts["expected"]["mean"],
                variance=ts["expected"]["variance"],
                frame_id=ts["expected"]["frame_id"],
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


PLATFORM_WRAPPER_PROVIDER = PlatformWrapperProvider()

register_golden_model_provider(PLATFORM_WRAPPER_PROVIDER.provider_id, PLATFORM_WRAPPER_PROVIDER)
