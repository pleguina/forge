#!/usr/bin/env python3
"""passthrough_demo stimulus generator.

Generates ``stimulus_current.svh`` for the passthrough_xsim flow: drives
data_in/data_in_valid for one cycle, then checks that data_out/data_out_valid
carry the same value one cycle later (the DUT's entire behavior).

Usage::

    python3 gen_stimulus.py --flow passthrough_xsim [--event-id <n>]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from forge.verify.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

# Golden events, matching schemas/data/passthrough_demo_golden.xml.
_EVENTS = {
    0: {"data_in": 0x3A, "data_in_valid": 1, "data_out": 0x3A, "data_out_valid": 1},
    1: {"data_in": 0x00, "data_in_valid": 0, "data_out": 0x00, "data_out_valid": 0},
}


def generate_for_flow(flow_name: str, event_id: int, out_path: Path) -> None:
    """Generate stimulus for *flow_name* event *event_id*.

    The framework guarantees that the testbench provides:
      - ``ap_clk``  — DUT clock (driven by TB)
      - ``ap_rst``  — synchronous active-high reset (driven by TB)

    Everything else is driven/checked here.
    """
    if event_id not in _EVENTS:
        raise ValueError(f"Unknown event id: {event_id} (known: {sorted(_EVENTS)})")
    ev = _EVENTS[event_id]

    em = StimulusEmitter()

    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    with em.event_block(f"event_{event_id}"):
        em.drive("pt_data_in", ev["data_in"], width=8)
        em.drive("pt_data_in_valid", ev["data_in_valid"], width=1)
        em.tick()  # posedge: DUT samples input, schedules registered output (NBA)
        em.tick()  # one more posedge: lets that NBA update settle before we read it

    with em.event_block("checks"):
        em.check("pt_data_out", ev["data_out"], width=8, label="data_out_check")
        em.check("pt_data_out_valid", ev["data_out_valid"], width=1, label="data_out_valid_check")

    with em.event_block("drain"):
        em.drive("pt_data_in_valid", 0, width=1)
        em.tick(cycles=2)

    write_run_stimulus_svh(em, out_path, header_comment=f"flow={flow_name} event={event_id}")
    print(f"  wrote {out_path}")


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
