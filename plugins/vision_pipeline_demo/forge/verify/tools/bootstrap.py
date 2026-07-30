#!/usr/bin/env python3
"""vision_pipeline_demo plugin registration — connects vision_pipeline_demo
to forge verify (release-plan Phase 10, slice 10.1).

After importing this module, vision_pipeline_demo is known to the
framework and forge verify generate / run will work for declared flows.
"""
from __future__ import annotations

# ── Plugin identity ──────────────────────────────────────────────────────
PLUGIN_ID = "vision_pipeline_demo"

_FLOW_KINDS: "list[str]" = [
    "hls_csim",
    "full_chip_rtl",
]

# ── Self-declare with the framework ────────────────────────────────────
from forge.verify.plugin_registry import declare_plugin_bootstrap as _declare
_declare(PLUGIN_ID, __name__)


def bootstrap() -> None:
    """Declare vision_pipeline_demo with the framework. Idempotent."""
    from forge.verify.plugin_registry import is_plugin_bootstrapped, mark_bootstrapped

    import dataset_adapter as _dataset_adapter  # noqa: F401  (self-registers on import)
    import golden_model_provider as _golden_model_provider  # noqa: F401  (self-registers on import)

    if is_plugin_bootstrapped(PLUGIN_ID):
        return
    mark_bootstrapped(
        PLUGIN_ID,
        backends=["csim", "xsim"],
        flow_kinds=_FLOW_KINDS,
    )
