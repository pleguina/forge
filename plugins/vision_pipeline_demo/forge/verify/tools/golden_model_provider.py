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
