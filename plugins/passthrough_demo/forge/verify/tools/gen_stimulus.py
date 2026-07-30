#!/usr/bin/env python3
"""passthrough_demo stimulus generator.

Generates ``stimulus_current.svh`` for the passthrough_xsim flow: drives
data_in/data_in_valid for one cycle, then checks that data_out/data_out_valid
carry the same value one cycle later (the DUT's entire behavior).

Reads its golden events for real from ``schemas/data/passthrough_demo_golden.xml``
via :class:`forge.verify.dataset_format.XmlDatasetLoader` (Phase 7, slice
7.4a) — this used to be a hardcoded Python dict that nothing ever
cross-checked against the XML file, so the declared golden data and the
simulated data could silently diverge. Now the XML file is the single
source of truth; edit it and the simulated behavior changes accordingly.

Usage::

    python3 gen_stimulus.py --flow passthrough_xsim [--event-id <n>]
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from forge.verify.dataset_adapter import DatasetSource
from forge.verify.dataset_service import DatasetService
from forge.verify.readmemh_stimulus import write_event_memory_file
from forge.verify.stimulus_helpers import (
    StimulusEmitter,
    emit_event_index_read,
    emit_runtime_output_check,
    write_readmemh_stimulus_svh,
    write_run_stimulus_svh,
)

_DATASET_XML = Path(__file__).resolve().parents[1] / "schemas/data/passthrough_demo_golden.xml"


def _ensure_bootstrapped() -> None:
    """Register `passthrough.identity-xml` before touching the adapter
    registry.  `forge test run` bootstraps the plugin itself before
    calling into this module, but a standalone `python3 gen_stimulus.py`
    invocation (e.g. from `run_trigger_demo.sh`-style scripts) does not
    go through that path, so this module must be able to bootstrap
    itself too."""
    import bootstrap as _bootstrap_mod
    _bootstrap_mod.bootstrap()


# ── readmemh-mode bit layout (Phase 7, slice 7.5) ──────────────────────
# One fixed-width word per event: data_in(8) | data_in_valid(1) |
# data_out(8) | data_out_valid(1) = 18 bits, MSB to LSB in that order.
_MEM_FILE_NAME = "passthrough_events.mem"
_WORD_WIDTH_BITS = 18


def _pack_event_word(ev: "Dict[str, Any]") -> str:
    data_in       = int(ev["in"]["data_in"], 0)
    data_in_valid = int(ev["in"]["data_in_valid"], 0)
    data_out       = int(ev["golden"]["data_out"], 0)
    data_out_valid = int(ev["golden"]["data_out_valid"], 0)
    word = (data_in << 10) | (data_in_valid << 9) | (data_out << 1) | data_out_valid
    hex_digits = (_WORD_WIDTH_BITS + 3) // 4
    return f"{word:0{hex_digits}x}"


def _load_events(dataset_path: Path = _DATASET_XML) -> "Dict[str, Dict[str, Any]]":
    """Load the real golden dataset, keyed by its real string event id.

    Routed through :class:`~forge.verify.dataset_service.DatasetService`
    (release-plan Phase 10, slice 10.0A) — the same layer-A (format
    loading, dispatched by *dataset_path*'s suffix, ``.xml``/``.json``)
    plus layer-B (`passthrough.identity-xml` adapter) path
    ``generate_readmemh_stimulus`` below already uses, so the
    `svh_include` and `readmemh` stimulus modes now agree on how a
    dataset is loaded, not only on what it contains.
    """
    _ensure_bootstrapped()
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=dataset_path))
    canonical = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {},
    )
    return dict(zip(canonical.metadata.event_ids, canonical.events))


def generate_for_flow(
    flow_name: str, event_id: int, out_path: Path, *, dataset_path: Path = _DATASET_XML,
) -> None:
    """Generate stimulus for *flow_name* event *event_id*.

    The framework guarantees that the testbench provides:
      - ``ap_clk``  — DUT clock (driven by TB)
      - ``ap_rst``  — synchronous active-high reset (driven by TB)

    Everything else is driven/checked here, read from the real dataset.
    """
    events = _load_events(dataset_path)
    key = str(event_id)
    if key not in events:
        raise ValueError(
            f"Unknown event id: {event_id} "
            f"(known: {sorted(events, key=int)}, dataset: {dataset_path})"
        )
    ev = events[key]
    data_in       = int(ev["in"]["data_in"], 0)
    data_in_valid = int(ev["in"]["data_in_valid"], 0)
    data_out       = int(ev["golden"]["data_out"], 0)
    data_out_valid = int(ev["golden"]["data_out_valid"], 0)

    em = StimulusEmitter()

    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    with em.event_block(f"event_{event_id}"):
        em.drive("pt_data_in", data_in, width=8)
        em.drive("pt_data_in_valid", data_in_valid, width=1)
        em.tick()  # posedge: DUT samples input, schedules registered output (NBA)
        em.tick()  # one more posedge: lets that NBA update settle before we read it

    with em.event_block("checks"):
        em.check("pt_data_out", data_out, width=8, label="data_out_check", event_id=event_id)
        em.check("pt_data_out_valid", data_out_valid, width=1, label="data_out_valid_check", event_id=event_id)

    with em.event_block("drain"):
        em.drive("pt_data_in_valid", 0, width=1)
        em.tick(cycles=2)

    write_run_stimulus_svh(em, out_path, header_comment=f"flow={flow_name} event={event_id}")
    print(f"  wrote {out_path}")


def generate_readmemh_stimulus(
    flow_name: str, flow_dir: Path, *, dataset_path: Path = _DATASET_XML,
) -> "Dict[int, str]":
    """Write the fixed-shape, non-recompiling readmemh stimulus mechanism
    (Phase 7, slice 7.5) once for *flow_dir*: a real ``.mem`` file (one
    fixed-width hex word per event, via the layer-A/layer-B dataset
    pipeline unchanged from ``generate_for_flow``) plus a content-stable
    ``stimulus_current.svh`` that indexes into it at runtime via
    ``+EVENT_INDEX=N`` — written once regardless of which event will
    eventually run, unlike ``generate_for_flow``'s per-event regeneration.

    Returns the real ``event_index → event_id`` mapping.
    """
    _ensure_bootstrapped()
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=dataset_path))
    canonical = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {},
    )

    mem_path = flow_dir / _MEM_FILE_NAME
    index_to_id = write_event_memory_file(canonical, _pack_event_word, mem_path)

    top_bit = _WORD_WIDTH_BITS - 1
    preamble = [
        f"reg [{top_bit}:0] event_mem [0:{len(index_to_id) - 1}];",
        # Absolute path: valid regardless of which work_dir a backend
        # actually simulates from (xsim vs. Verilator use different work
        # dir conventions) — no staging step needed.
        f'initial $readmemh("{mem_path.resolve()}", event_mem);',
    ]

    body: "list[str]" = [
        "    integer event_index;",
        f"    reg [{top_bit}:0] event_word;",
        "    reg [7:0] exp_data_out;",
        "    reg exp_data_out_valid;",
        "",
        emit_event_index_read("event_index"),
        "    event_word = event_mem[event_index];",
        "",
        "    ap_rst <= 1'b1;",
        "    @(posedge ap_clk);",
        "    @(posedge ap_clk);",
        "    @(posedge ap_clk);",
        "    @(posedge ap_clk);",
        "    ap_rst <= 1'b0;",
        "    @(posedge ap_clk);",
        "",
        "    pt_data_in <= event_word[17:10];",
        "    pt_data_in_valid <= event_word[9];",
        "    exp_data_out = event_word[8:1];",
        "    exp_data_out_valid = event_word[0];",
        "    @(posedge ap_clk);",
        "    @(posedge ap_clk);",
        "",
    ]
    body += emit_runtime_output_check("pt_data_out", "exp_data_out", width=8, label="data_out_check")
    body += emit_runtime_output_check("pt_data_out_valid", "exp_data_out_valid", width=1, label="data_out_valid_check")
    body += [
        "",
        "    pt_data_in_valid <= 1'b0;",
        "    @(posedge ap_clk);",
        "    @(posedge ap_clk);",
    ]

    out_path = flow_dir / "stimulus_current.svh"
    write_readmemh_stimulus_svh(
        preamble_lines=preamble,
        task_body_lines=body,
        out_path=out_path,
        header_comment=f"flow={flow_name} stimulus_mode=readmemh",
    )
    print(f"  wrote {out_path} (readmemh, {len(index_to_id)} event(s) in {mem_path.name})")
    return index_to_id


def main() -> None:
    ap = argparse.ArgumentParser(description="passthrough_demo stimulus generator")
    ap.add_argument("--flow",     required=True, help="Flow name from design.verification.yml")
    ap.add_argument("--event-id", dest="event_id", type=int, default=0)
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, args.event_id, out_path)


if __name__ == "__main__":
    main()
