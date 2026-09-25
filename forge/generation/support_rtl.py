"""Framework support RTL — the small library of modules the generators
instantiate *between* a design's own modules (pipeline registers, delay
lines, SLR-crossing registers, CDC synchronizers).

These ship inside the forge package (``forge/rtl/support/``) rather than in
any one plugin, because the generators own their interface: parameter and
port names (``DATAWIDTH``/``STAGES``, ``WIDTH``/``DEPTH``, ``din``/``dout``,
``dst_clk``, …) are hardcoded in ``write_structural_verilog`` and
``write_bd_tcl``, and ``slr_crossing_delay``'s internal register names are
hardcoded into ``algo_top.crossings.json``. Versioning the RTL with the
generator that depends on it is the only way to keep the two in step.

A plugin can still supply its own implementation of any of these modules:
if the design's own compile set already contains a file with the same name
(e.g. a ``signal_delay`` module declared in ``modules.yml`` with
``src: [.../signal_delay.v]``), the framework copy is not added — see
``plugin_provided``. The plugin's copy must keep the same parameter and port
names, since the generated top level still instantiates it by those names.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

#: Directory holding the framework support RTL, packaged as package data.
SUPPORT_RTL_DIR = Path(__file__).resolve().parent.parent / "rtl" / "support"

#: Which support-RTL file each canonical-IR transformation kind needs. The
#: kinds are the IR's own vocabulary (``forge.ir.model.ResolvedTransformation``)
#: — a kind with no RTL of its own (fanout, gather_scatter, tie_off, the
#: reserved adapter kinds) is simply absent from this map.
SUPPORT_RTL_BY_TRANSFORMATION: Dict[str, str] = {
    "pipeline_register":  "RegisterStage.v",
    "latency_delay":      "signal_delay.v",
    "slr_crossing":       "slr_crossing_delay.v",
    "cdc_synchronizer":   "cdc_sync2ff.v",
    "pulse_sync":         "cdc_pulse_sync.v",
    "mailbox_transfer":   "cdc_mailbox.v",
    "async_fifo":         "cdc_async_fifo.v",
    "reset_synchronizer": "cdc_reset_sync.v",
}


def support_rtl_path(filename: str) -> Path:
    """Absolute path of the framework's own copy of *filename*.

    Raises ``FileNotFoundError`` if it isn't there — that's a broken forge
    install (package data missing), never a plugin-layout problem.
    """
    path = SUPPORT_RTL_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"framework support RTL {filename} missing from {SUPPORT_RTL_DIR} "
            "— the forge install is incomplete (forge/rtl/support/*.v package data)"
        )
    return path


def plugin_provided(filename: str, compile_files: Iterable[str]) -> Optional[str]:
    """The design's own file named *filename*, if its compile set has one.

    A plugin that declares its own ``signal_delay``/``slr_crossing_delay``/…
    module keeps using it; adding the framework copy as well would give the
    compile two definitions of the same module.
    """
    for f in compile_files:
        if Path(f).name == filename:
            return str(f)
    return None


def resolve_support_rtl(filenames: Iterable[str], compile_files: Iterable[str] = ()) -> List[Path]:
    """Framework support-RTL files to add to a compile set that already
    contains *compile_files* — every name in *filenames*, minus any the
    design already provides itself (``plugin_provided``)."""
    existing = list(compile_files)
    return [
        support_rtl_path(name)
        for name in filenames
        if plugin_provided(name, existing) is None
    ]
