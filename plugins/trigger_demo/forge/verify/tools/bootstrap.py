#!/usr/bin/env python3
"""trigger_demo plugin registration — connects trigger_demo to forge.verify.

After VF_ACTION_PLAN_3: backend registration (xsim, csim) is no longer
needed here.  The framework auto-registers both backends at import time.
This bootstrap only declares plugin identity and marks it bootstrapped.
"""
from __future__ import annotations

# ── Plugin identity ──────────────────────────────────────────────────────
PLUGIN_ID = "trigger_demo"

_FLOW_KINDS: list[str] = [
    "hls_csim",
    "single_module_rtl",
    "full_chip_rtl",
]

# ── Self-declare with the framework ──────────────────────────────────────
from forge.verify.plugin_registry import declare_plugin_bootstrap as _declare
_declare(PLUGIN_ID, __name__)


# ── Bootstrap function ───────────────────────────────────────────────────

def bootstrap() -> None:
    """Declare trigger_demo with the framework.  Idempotent.

    Framework-owned backends (xsim, csim) are auto-registered at
    ``import forge.verify`` time — no explicit registration needed here.
    """
    from forge.verify.plugin_registry import is_plugin_bootstrapped, mark_bootstrapped

    if is_plugin_bootstrapped(PLUGIN_ID):
        return

    mark_bootstrapped(
        PLUGIN_ID,
        backends=["csim", "xsim"],   # framework-owned; listed for introspection only
        flow_kinds=_FLOW_KINDS,
    )
