#!/usr/bin/env python3
"""passthrough_demo plugin registration — connects passthrough_demo to forge verify.

After importing this module, passthrough_demo is known to the framework and
forge verify generate / run will work for declared flows.
"""
from __future__ import annotations

# ── Plugin identity ──────────────────────────────────────────────────────
PLUGIN_ID = "passthrough_demo"

_FLOW_KINDS: list[str] = [
    "full_chip_rtl",
]

# ── Self-declare with the framework ────────────────────────────────────
from forge.verify.plugin_registry import declare_plugin_bootstrap as _declare
_declare(PLUGIN_ID, __name__)


def bootstrap() -> None:
    """Declare passthrough_demo with the framework.  Idempotent."""
    from forge.verify.plugin_registry import is_plugin_bootstrapped, mark_bootstrapped
    if is_plugin_bootstrapped(PLUGIN_ID):
        return
    mark_bootstrapped(
        PLUGIN_ID,
        backends=["csim", "xsim"],
        flow_kinds=_FLOW_KINDS,
    )
