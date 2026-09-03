"""The one place FORGE decides whether a port name means "clock" or "reset".

This convention was written out by hand in four places — the structural
Verilog generator, the structural VHDL generator, the contract skeleton
generator, and project discovery — and they did not agree. All of them
recognised ``clk``, ``ap_clk`` and anything ending ``_clk``; none recognised
the fused bus spellings that most of the industry actually uses. The
consequences were not cosmetic:

* An APB peripheral's ``pclk`` matched nothing, so the generator's
  clock fan-out skipped it and the pin fell through to the
  unconnected-input path — the design's clock was tied to ``1'b0``, in a
  top level that elaborates perfectly and does nothing.
* ``presetn``/``s_axi_aresetn`` matched nothing either, so an AXI or APB
  block's reset was tied to ground the same way.
* In discovery, a vendor IP with ``s_axi_aclk``/``m_axi_aclk``/``ref_clk``
  read as *single*-clock, so it was never flagged as outside FORGE's
  one-functional-clock-per-module envelope.

Hence one module, in ``forge/core`` so every layer can use it (``core``
depends on nothing, which is what makes it a safe shared home).

Deliberately tight. The fused form is recognised only for a **single**
leading letter — the real bus prefixes, ``p``/``a``/``h``/``s``/``m``/``n``
— so ``pclk``, ``aclk``, ``presetn`` and ``aresetn`` resolve while
``burst`` does not become a reset because it happens to end in ``rst``.
Anything longer must be ``_``-separated, which every remaining convention
already is (``ref_clk``, ``sys_clk``, ``s_axi_aclk``).
"""

from __future__ import annotations

import re
from typing import Tuple

#: A ``_``-separated part equal to one of these *is* the signal.
_CLOCK_WORDS = ("clk", "clock", "clki", "clkin")
_RESET_WORDS = ("rst", "reset", "rstn", "resetn", "arst", "areset",
                "aresetn", "nrst", "srst", "sresetn", "presetn")

#: The fused spellings: one letter, then the word. ``pclk``, ``aclk``,
#: ``hclk``, ``sclk``, ``mclk``, ``presetn``, ``aresetn``, ``nrst``.
_FUSED_CLOCK_RE = re.compile(r"^[a-z](?:clk|clock)$")
_FUSED_RESET_RE = re.compile(r"^[a-z](?:rst|rstn|reset|resetn)$")

#: Multi-letter fused forms common enough to be worth naming explicitly.
#: An allowlist rather than a pattern, so widening it stays a deliberate act.
_EXTRA_CLOCKS = ("refclk", "sysclk", "usrclk", "txclk", "rxclk", "coreclk")
_EXTRA_RESETS = ("sysrst", "corerst", "gsr")

#: A trailing ``n`` directly after a reset word means active-low
#: (``rst_n``, ``resetn``, ``aresetn``, ``presetn``, ``s_axi_aresetn``); a
#: leading ``n_`` is the other spelling.
_ACTIVE_LOW_RE = re.compile(r"(?:^n_)|(?:_n$)|(?:(?:rst|reset)n$)", re.I)


def _parts(name: str) -> Tuple[str, ...]:
    return tuple(part for part in name.lower().split("_") if part)


def is_clock_name(name: str) -> bool:
    """Whether *name* reads as a clock by convention.

    Never a substring search: ``block_count`` is not a clock, and
    ``unlock_key`` is not one either.
    """
    lowered = name.lower()
    if lowered in _CLOCK_WORDS or lowered in _EXTRA_CLOCKS:
        return True
    for part in _parts(name):
        if part in _CLOCK_WORDS or part in _EXTRA_CLOCKS:
            return True
        if _FUSED_CLOCK_RE.match(part):
            return True
    return False


def is_reset_name(name: str) -> bool:
    """Whether *name* reads as a reset by convention.

    A name that reads as a clock never reads as a reset, so a hypothetical
    ``rst_clk`` resolves one way rather than both.
    """
    if is_clock_name(name):
        return False
    lowered = name.lower()
    if lowered in _RESET_WORDS or lowered in _EXTRA_RESETS:
        return True
    for part in _parts(name):
        if part in _RESET_WORDS or part in _EXTRA_RESETS:
            return True
        if _FUSED_RESET_RE.match(part):
            return True
    return False


def is_active_low_reset(name: str) -> bool:
    """Whether a reset port name declares itself active-low.

    A *convention*, never proof — callers record it as heuristic evidence.
    """
    return bool(_ACTIVE_LOW_RE.search(name.lower()))
