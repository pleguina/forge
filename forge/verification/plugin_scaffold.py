"""The plugin skeleton ``forge dev verify init-plugin`` writes."""
from __future__ import annotations

from pathlib import Path


_BOOTSTRAP_TEMPLATE = '''\
#!/usr/bin/env python3
"""{plugin_id} plugin registration — connects {plugin_id} to forge verify.

After importing this module, {plugin_id} is known to the framework and
forge dev verify generate / run will work for declared flows.
"""
from __future__ import annotations

# ── Plugin identity ──────────────────────────────────────────────────────
PLUGIN_ID = "{plugin_id}"

_FLOW_KINDS: list[str] = [
    "full_chip_rtl",
]

# ── Self-declare with the framework ────────────────────────────────────
from forge.verification.plugin_registry import declare_plugin_bootstrap as _declare
_declare(PLUGIN_ID, __name__)


def bootstrap() -> None:
    """Declare {plugin_id} with the framework.  Idempotent."""
    from forge.verification.plugin_registry import is_plugin_bootstrapped, mark_bootstrapped
    if is_plugin_bootstrapped(PLUGIN_ID):
        return
    mark_bootstrapped(
        PLUGIN_ID,
        backends=["csim", "xsim"],
        flow_kinds=_FLOW_KINDS,
    )
'''


_GEN_STIMULUS_TEMPLATE = '''\
#!/usr/bin/env python3
"""{plugin_id} stimulus generator.

Generates ``stimulus_current.svh`` for the {plugin_id}_xsim flow: drives
{plugin_id}_data_in/{plugin_id}_data_in_valid for one cycle, then checks
that {plugin_id}_data_out/{plugin_id}_data_out_valid carry the same value
one cycle later — the scaffolded RTL stub's entire behavior (see
algo/rtl/{plugin_id}.v). Matches plugins/passthrough_demo's real, working
gen_stimulus.py byte-for-byte in structure (only identifiers differ) —
confirmed runnable with zero manual edits, since the scaffolded RTL is
itself byte-for-byte identical to passthrough_demo's real RTL.

Usage::

    python3 gen_stimulus.py --flow {plugin_id}_xsim [--event-id <n>]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from forge.verification.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

# Golden events, matching schemas/data/{plugin_id}_golden.xml.
_EVENTS = {{
    0: {{"data_in": 0x3A, "data_in_valid": 1, "data_out": 0x3A, "data_out_valid": 1}},
    1: {{"data_in": 0x00, "data_in_valid": 0, "data_out": 0x00, "data_out_valid": 0}},
}}


def generate_for_flow(flow_name: str, event_id: int, out_path: Path) -> None:
    """Generate stimulus for *flow_name* event *event_id*.

    The framework guarantees that the testbench provides:
      - ``ap_clk``  — DUT clock (driven by TB)
      - ``ap_rst``  — synchronous active-high reset (driven by TB)

    Everything else is driven/checked here.
    """
    if event_id not in _EVENTS:
        raise ValueError(f"Unknown event id: {{event_id}} (known: {{sorted(_EVENTS)}})")
    ev = _EVENTS[event_id]

    em = StimulusEmitter()

    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    with em.event_block(f"event_{{event_id}}"):
        em.drive("{plugin_id}_data_in", ev["data_in"], width=8)
        em.drive("{plugin_id}_data_in_valid", ev["data_in_valid"], width=1)
        em.tick()  # posedge: DUT samples input, schedules registered output (NBA)
        em.tick()  # one more posedge: lets that NBA update settle before we read it

    with em.event_block("checks"):
        em.check("{plugin_id}_data_out", ev["data_out"], width=8, label="data_out_check", event_id=event_id)
        em.check("{plugin_id}_data_out_valid", ev["data_out_valid"], width=1, label="data_out_valid_check", event_id=event_id)

    with em.event_block("drain"):
        em.drive("{plugin_id}_data_in_valid", 0, width=1)
        em.tick(cycles=2)

    write_run_stimulus_svh(em, out_path, header_comment=f"flow={{flow_name}} event={{event_id}}")
    print(f"  wrote {{out_path}}")


def main() -> None:
    ap = argparse.ArgumentParser(description="{plugin_id} stimulus generator")
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
'''


_GOLDEN_XML_TEMPLATE = '''\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  {plugin_id} — Golden stimulus and expected results

  The scaffolded RTL stub (algo/rtl/{plugin_id}.v) registers
  data_in/data_in_valid onto data_out/data_out_valid one clock cycle
  later, unchanged — matching plugins/passthrough_demo's real RTL
  byte-for-byte. Two events, both straightforward. Replace with your
  real algorithm's golden data once you replace the RTL stub.
-->
<{plugin_id}_events>

  <event id="0">
    <in data_in="0x3A" data_in_valid="1"/>
    <golden data_out="0x3A" data_out_valid="1"/>
  </event>

  <event id="1">
    <in data_in="0x00" data_in_valid="0"/>
    <golden data_out="0x00" data_out_valid="0"/>
  </event>

</{plugin_id}_events>
'''


_DESIGN_VERIFICATION_TEMPLATE = '''\
# ═══════════════════════════════════════════════════════════════════════
# {plugin_id} — Verification Design Contract
# ═══════════════════════════════════════════════════════════════════════
#
#   forge dev topgen gen-top   plugins/{plugin_id}/forge/designs/design.yml --mode verilog ...
#   forge dev verify generate  plugins/{plugin_id}/forge/verify/design.verification.yml
#   forge dev verify doctor    plugins/{plugin_id}/forge/verify/design.verification.yml
#   forge dev verify run       plugins/{plugin_id}/forge/verify/{plugin_id}_xsim/verify.flow.yml --plugin {plugin_id}
#
# Immediately runnable with zero manual edits — the scaffolded RTL stub
# (algo/rtl/{plugin_id}.v) is byte-for-byte identical to
# plugins/passthrough_demo's real, working reference RTL, and this file
# (plus tools/gen_stimulus.py and schemas/data/{plugin_id}_golden.xml) is
# the matching real, working verify-side counterpart — not a stub
# requiring manual editing before first use.
# ───────────────────────────────────────────────────────────────────────
plugin: {plugin_id}

# ─── Datasets ─────────────────────────────────────────────────────────
datasets:
  {plugin_id}_golden:
    xml: schemas/data/{plugin_id}_golden.xml
    description: Golden reference dataset for {plugin_id}

# ─── Simulation Defaults ──────────────────────────────────────────────
defaults:
  clk_period_ns:              4.0
  reset_cycles:               4
  idle_cycles_after_reset:    0
  post_stimulus_drain_cycles: 4

# ─── Flows ────────────────────────────────────────────────────────────
# One module ({plugin_id}) that IS the whole design, so it's verified as
# a full_chip_rtl flow against the topgen-generated algo_top.v — no HLS
# synthesis step needed at all, since the module is hand-written RTL.
flows:

  - name:             {plugin_id}_xsim
    kind:             full_chip_rtl
    backend:          xsim
    top_module:       algo_top
    tb_module:        tb_algo_top
    dut_rtl_source:   gen-top/design_{plugin_id}
    # `forge test` with no arguments regenerates algo_top.v there from this design.
    design:           ../designs/design.yml
    dataset:          {plugin_id}_golden
    default_event_id: 0
    simulation:
      reset_cycles:               4
      idle_cycles_after_reset:    0
      post_stimulus_drain_cycles: 4
    xsim:
      top_lib: xil_defaultlib
      use_sv_flag: false
    coverage_intent:  functional
'''


_PLUGIN_TEST_TEMPLATE = '''\
"""{plugin_id}'s verification files, checked without a simulator.

These say the contract is usable, not that the design works: that is
`forge test`, which simulates each flow against its dataset.
"""

from pathlib import Path

from forge.verification.design_contract import load_verify_design

VERIFY = Path(__file__).resolve().parents[1]


def test_every_flow_names_a_dataset_that_exists() -> None:
    contract = load_verify_design(VERIFY / "design.verification.yml")
    assert contract.flows
    datasets = {{d.name: d for d in contract.datasets}}
    for flow in contract.flows:
        assert (VERIFY / datasets[flow.dataset].xml).is_file(), flow.name
'''


def scaffold_plugin(plugin_id: str, plugins_root: "str | Path", *, dry_run: bool = False) -> int:
    """Scaffold a new plugin skeleton under
    ``<plugins_root>/<plugin_id>/forge/verify/``.
    """
    plugins_root = Path(plugins_root)
    verify_root  = plugins_root / plugin_id / "forge" / "verify"
    tools_dir    = verify_root / "tools"
    schemas_dir  = verify_root / "schemas" / "data"
    tests_dir    = verify_root / "tests"

    files_to_create = {
        verify_root / "design.verification.yml":
            _DESIGN_VERIFICATION_TEMPLATE.format(plugin_id=plugin_id),
        tools_dir / "bootstrap.py":
            _BOOTSTRAP_TEMPLATE.format(plugin_id=plugin_id),
        tools_dir / "gen_stimulus.py":
            _GEN_STIMULUS_TEMPLATE.format(plugin_id=plugin_id),
        tests_dir / "__init__.py": "",
        tests_dir / f"test_{plugin_id}_verify.py": _PLUGIN_TEST_TEMPLATE.format(plugin_id=plugin_id),
    }

    if dry_run:
        print(f"[init-plugin] Would create plugin skeleton for {plugin_id!r}:")
        for path in sorted(files_to_create):
            print(f"  {path}")
        return 0

    created: list[Path] = []
    skipped: list[Path] = []
    for dest_path, content in files_to_create.items():
        if dest_path.exists():
            skipped.append(dest_path)
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(content)
        created.append(dest_path)

    # Real golden dataset (2 real events, matching gen_stimulus.py's
    # _EVENTS) — not a stub requiring manual editing (the RTL stub is
    # byte-for-byte identical to passthrough_demo's real RTL, so
    # passthrough_demo's real golden data is directly reusable here,
    # parametrized by plugin_id).
    golden_xml = schemas_dir / f"{plugin_id}_golden.xml"
    if not golden_xml.exists():
        schemas_dir.mkdir(parents=True, exist_ok=True)
        golden_xml.write_text(_GOLDEN_XML_TEMPLATE.format(plugin_id=plugin_id))
        created.append(golden_xml)

    print(f"[init-plugin] Plugin {plugin_id!r} scaffolded:")
    for p in created:
        print(f"  created  {p}")
    for p in skipped:
        print(f"  skipped  {p}  (already exists)")

    print(f"\nNext steps (no manual editing required):")
    print(f"  1. Run: forge dev verify generate {verify_root / 'design.verification.yml'}")
    print(f"  2. Run: forge dev verify doctor   {verify_root / 'design.verification.yml'}")
    print(f"  3. Run: forge dev verify run      {verify_root / (plugin_id + '_xsim') / 'verify.flow.yml'} --plugin {plugin_id}")
    return 0
