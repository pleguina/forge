"""Predict the RTL ports Vitis HLS will generate for an HLS module.

An interface contract for an HLS module has to name real RTL ports, but
those ports don't exist until the IP is built — and the names shift
substantially with the interface pragma and the argument's shape. The same
``hit_t[18][14]`` argument becomes 252 scalar ports under
``ap_none`` + ``ARRAY_PARTITION complete dim=0``, and a set of BRAM
interfaces (``_address0``/``_ce0``/``_q0``) under the default ``ap_memory``
with ``dim=1``. Both shapes are real and live in this repository's own
history.

This module predicts that mapping statically, so a contract can be drafted
(and reviewed) before the first synthesis run.

Scope
-----
The rules encoded here were derived from **real Vitis HLS 2024.1 output**,
not from documentation: see ``forge/tests/fixtures/hls_port_matrix/``,
which holds the C++ sources and the exact port lists they synthesise to.

Predicted reliably:

* block protocol — ``ap_ctrl_none`` / ``ap_ctrl_hs`` / ``ap_ctrl_chain``
* scalar arguments in every mode — ``ap_none``, ``ap_vld``, ``ap_ack``,
  ``ap_hs``, ``ap_stable``, in both directions
* arrays — full scalarisation, per-dimension partitioning, ``ap_memory``
  (read and write), ``ap_fifo``
* ``axis`` streams, including TDATA's round-up to a byte multiple
* return-by-value → ``ap_return``

Deliberately **not** predicted, because the matrix shows they can't be
derived from the source alone:

* ``s_axilite`` / ``m_axi`` bundles — the port set depends on address and
  data widths chosen at synthesis, and adding either flips the block reset
  from ``ap_rst`` to ``ap_rst_n``
* the packed width of a struct — HLS pads members rather than concatenating
  them (a 16-bit-logical struct in the fixture synthesises to 48 bits), so
  a summed width is wrong in a way that looks plausible

Anything unpredicted is reported, never guessed. A prediction is a draft to
be confirmed against the built IP with ``forge core verify-contract``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

#: Block-level protocols and the control ports each adds, in HLS's own
#: emission order. ``ap_clk``/``ap_rst`` are added separately because their
#: presence is unconditional and their *polarity* is not (see module docs).
BLOCK_PROTOCOL_PORTS: Dict[str, Sequence[str]] = {
    "ap_ctrl_none": (),
    "ap_ctrl_hs": ("ap_start", "ap_done", "ap_idle", "ap_ready"),
    "ap_ctrl_chain": ("ap_start", "ap_done", "ap_idle", "ap_ready", "ap_continue"),
}

#: Interfaces whose presence flips the block reset to active-low.
_ACTIVE_LOW_RESET_MODES = frozenset({"s_axilite", "m_axi", "axis"})

#: Modes this module refuses to predict — see the module docstring.
UNPREDICTABLE_MODES = frozenset({"s_axilite", "m_axi", "bram"})


@dataclass(frozen=True)
class PredictedPort:
    """One RTL port predicted for an HLS argument."""
    name: str
    direction: str          # "input" | "output"
    width: int
    role_hint: str          # the C++ argument it came from ("" for control)
    kind: str = "data"      # data | control | handshake | memory | stream


@dataclass
class Argument:
    """One argument of the HLS top function, as declared in C++."""
    name: str
    width: int                       # element width in bits
    is_output: bool = False          # declared by reference / written array
    dims: Sequence[int] = ()         # array dimensions, outermost first
    mode: Optional[str] = None       # explicit `#pragma HLS INTERFACE` mode
    partition_dim: Optional[int] = None   # ARRAY_PARTITION complete dim=N
    is_struct: bool = False


@dataclass
class Prediction:
    ports: List[PredictedPort] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def by_name(self) -> Dict[str, PredictedPort]:
        return {p.name: p for p in self.ports}


def _addr_width(depth: int) -> int:
    """Address width HLS gives a memory port of *depth* elements."""
    return max(1, math.ceil(math.log2(depth))) if depth > 1 else 1


def _byte_round_up(width: int) -> int:
    """AXI-Stream TDATA is widened to a whole number of bytes."""
    return max(8, math.ceil(width / 8) * 8)


def _flatten_indices(dims: Sequence[int]) -> List[str]:
    """`_i_j` suffixes for every element of an array of *dims*."""
    suffixes = [""]
    for d in dims:
        suffixes = [f"{s}_{i}" for s in suffixes for i in range(d)]
    return suffixes


def _scalar_ports(arg: Argument, base: str) -> List[PredictedPort]:
    """A scalar argument's ports under its interface mode.

    Handshake direction follows the data direction: on an input, ``_ap_vld``
    is an input and ``_ap_ack`` an output; on an output they swap. Confirmed
    against the synthesised fixture rather than assumed.

    With no explicit ``#pragma HLS INTERFACE``, a by-value input defaults to
    ``ap_none`` but an output passed by reference defaults to ``ap_vld`` and
    so gains a ``_ap_vld`` strobe. Found on a real module: inputProcessor
    declares no INTERFACE pragmas at all, and its ``proc_out`` reference
    output has a ``proc_out_ap_vld`` port that assuming ``ap_none``
    everywhere would miss.
    """
    mode = arg.mode or ("ap_vld" if arg.is_output else "ap_none")
    d = "output" if arg.is_output else "input"
    opposite = "input" if arg.is_output else "output"
    ports = [PredictedPort(base, d, arg.width, arg.name)]
    if mode in ("ap_vld", "ap_hs"):
        ports.append(PredictedPort(f"{base}_ap_vld", d, 1, arg.name, "handshake"))
    if mode in ("ap_ack", "ap_hs"):
        ports.append(PredictedPort(f"{base}_ap_ack", opposite, 1, arg.name, "handshake"))
    return ports


def _memory_ports(name: str, depth: int, width: int, arg_name: str,
                  *, writable: bool) -> List[PredictedPort]:
    """One ``ap_memory`` interface: address/ce/q, plus we/d when written."""
    aw = _addr_width(depth)
    ports = [
        PredictedPort(f"{name}_address0", "output", aw, arg_name, "memory"),
        PredictedPort(f"{name}_ce0", "output", 1, arg_name, "memory"),
    ]
    if writable:
        ports.append(PredictedPort(f"{name}_we0", "output", 1, arg_name, "memory"))
        ports.append(PredictedPort(f"{name}_d0", "output", width, arg_name, "memory"))
    else:
        ports.append(PredictedPort(f"{name}_q0", "input", width, arg_name, "memory"))
    return ports


def _array_ports(arg: Argument, pred: Prediction) -> List[PredictedPort]:
    mode = arg.mode or "ap_memory"
    dims = list(arg.dims)

    if mode == "ap_fifo":
        d_in = not arg.is_output
        if d_in:
            return [
                PredictedPort(f"{arg.name}_dout", "input", arg.width, arg.name, "stream"),
                PredictedPort(f"{arg.name}_empty_n", "input", 1, arg.name, "stream"),
                PredictedPort(f"{arg.name}_read", "output", 1, arg.name, "stream"),
            ]
        return [
            PredictedPort(f"{arg.name}_din", "output", arg.width, arg.name, "stream"),
            PredictedPort(f"{arg.name}_full_n", "input", 1, arg.name, "stream"),
            PredictedPort(f"{arg.name}_write", "output", 1, arg.name, "stream"),
        ]

    if mode == "axis":
        w = _byte_round_up(arg.width)
        d = "output" if arg.is_output else "input"
        opp = "input" if arg.is_output else "output"
        return [
            PredictedPort(f"{arg.name}_TDATA", d, w, arg.name, "stream"),
            PredictedPort(f"{arg.name}_TVALID", d, 1, arg.name, "stream"),
            PredictedPort(f"{arg.name}_TREADY", opp, 1, arg.name, "stream"),
        ]

    # Partitioning decides how much of the array becomes scalars and how
    # much stays a memory. `complete dim=0` scalarises every dimension;
    # `complete dim=N` splits that one dimension, leaving the rest a memory
    # per element. Vitis defaults a bare `complete` to dim=1, which is why
    # two identically-shaped arrays in this repo's history produced
    # completely different port sets.
    pdim = arg.partition_dim
    if pdim == 0 or (mode == "ap_none" and pdim is None and len(dims) == 0):
        scalarised, remaining = dims, []
    elif pdim is not None and 1 <= pdim <= len(dims):
        scalarised, remaining = dims[:pdim], dims[pdim:]
    else:
        scalarised, remaining = [], dims

    if mode == "ap_none" and remaining:
        pred.warnings.append(
            f"{arg.name}: ap_none on an array with unpartitioned dimensions "
            f"{remaining} — HLS cannot expose that as scalars; expected a "
            f"complete partition of every dimension"
        )

    ports: List[PredictedPort] = []
    for suffix in _flatten_indices(scalarised):
        base = f"{arg.name}{suffix}"
        if remaining:
            depth = 1
            for d in remaining:
                depth *= d
            if not any(w.startswith(f"{arg.name}: memory") for w in pred.warnings):
                # HLS adds a second port set (_address1/_ce1/_q1/_d1/_we1) when
                # the schedule needs two accesses per cycle. That is a
                # scheduler decision driven by II and the access pattern, not
                # anything the source declares — the single-port form is
                # predicted, and the caller is told the other may appear.
                # Seen on a real module: inputProcessor's hits_in, where every
                # port the prediction missed was exactly this second set.
                pred.warnings.append(
                    f"{arg.name}: memory interface predicted single-port. HLS "
                    f"adds a second port set (_address1/_ce1/_q1) when the "
                    f"schedule needs two accesses per cycle — confirm against "
                    f"the built IP"
                )
            ports.extend(_memory_ports(base, depth, arg.width, arg.name,
                                       writable=arg.is_output))
        else:
            ports.append(PredictedPort(
                base, "output" if arg.is_output else "input", arg.width, arg.name))
    return ports


def predict_ports(
    args: Sequence[Argument],
    *,
    block_protocol: str = "ap_ctrl_hs",
    returns_value: bool = False,
    return_width: Optional[int] = None,
) -> Prediction:
    """Predict the RTL port list for one HLS top function.

    Parameters mirror what a static read of the C++ yields: the argument
    list with resolved widths and dimensions, the block-level protocol from
    ``#pragma HLS INTERFACE ... port=return``, and whether the function
    returns a value.
    """
    pred = Prediction()

    if block_protocol not in BLOCK_PROTOCOL_PORTS:
        pred.warnings.append(
            f"unknown block protocol {block_protocol!r}; assuming ap_ctrl_hs")
        block_protocol = "ap_ctrl_hs"

    active_low = any((a.mode or "") in _ACTIVE_LOW_RESET_MODES for a in args)
    pred.ports.append(PredictedPort("ap_clk", "input", 1, "", "control"))
    pred.ports.append(PredictedPort(
        "ap_rst_n" if active_low else "ap_rst", "input", 1, "", "control"))
    if active_low:
        pred.warnings.append(
            "an AXI-family interface is present, so the block reset is "
            "ap_rst_n (active-low) rather than ap_rst")

    for name in BLOCK_PROTOCOL_PORTS[block_protocol]:
        direction = "input" if name in ("ap_start", "ap_continue") else "output"
        pred.ports.append(PredictedPort(name, direction, 1, "", "control"))

    for arg in args:
        mode = arg.mode or ("ap_memory" if arg.dims else "ap_none")
        if mode in UNPREDICTABLE_MODES:
            pred.warnings.append(
                f"{arg.name}: {mode} port set depends on address/data widths "
                f"chosen at synthesis — not predicted; build the IP and read "
                f"the real ports"
            )
            continue
        if arg.is_struct:
            pred.warnings.append(
                f"{arg.name}: struct width is not predicted — HLS pads members "
                f"rather than concatenating them (see the port matrix fixture, "
                f"where a 16-bit-logical struct synthesises to 48 bits)"
            )
        if arg.dims:
            pred.ports.extend(_array_ports(arg, pred))
        else:
            pred.ports.extend(_scalar_ports(arg, arg.name))

    if returns_value:
        if return_width is None:
            pred.warnings.append(
                "ap_return width unknown (a struct return is padded, not summed)")
        pred.ports.append(PredictedPort(
            "ap_return", "output", return_width or 0, "return"))

    return pred
