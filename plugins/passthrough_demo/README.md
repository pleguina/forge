# passthrough_demo

The **minimal FORGE reference plugin**. One RTL module, one clock-cycle of
registered passthrough, no HLS dependency, no algorithm-specific vocabulary
anywhere in it. It exists to prove the FORGE pipeline — topology generation,
contract wiring, verification flow generation, and real xsim simulation —
works end to end for a genuinely generic consumer, and to give a second,
much simpler reference point alongside `plugins/trigger_demo/` (which is
realistic and CMS-flavored, but consequently bigger).

If you're looking for a complete, richer example (multiple modules, HLS,
all five topology patterns), see `plugins/trigger_demo/` and its
`CANONICAL_PATTERNS.md`. If you just want to see the smallest possible
thing that works, you're in the right place.

## What it does

```
data_in, data_in_valid ──[registered 1 cycle]──> data_out, data_out_valid
```

That's the whole algorithm. See `algo/rtl/passthrough.v`.

## Layout

```
plugins/passthrough_demo/
├── algo/rtl/passthrough.v                    ← the entire "algorithm"
├── forge/
│   ├── modules.yml                           ← authored: 1 module, kind=rtl
│   ├── designs/design.yml                    ← authored: 1 instance, no connections
│   ├── interfaces/passthrough.interface.yaml ← authored: port role contract
│   └── verify/
│       ├── design.verification.yml           ← authored: 1 flow (full_chip_rtl, xsim)
│       ├── schemas/data/passthrough_demo_golden.xml  ← authored: golden dataset
│       ├── tools/
│       │   ├── bootstrap.py                  ← authored: plugin registration
│       │   └── gen_stimulus.py               ← authored: xsim stimulus generator
│       ├── tests/                            ← authored: contract unit tests
│       └── passthrough_xsim/                 ← generated: verify.flow.yml, tb, wave.tcl
└── README.md
```

## Running it

```bash
pip install -e forge/

forge topgen validate plugins/passthrough_demo/forge/designs/design.yml

forge topgen gen-top plugins/passthrough_demo/forge/designs/design.yml \
  --mode verilog \
  --consumer-root . \
  --contracts-from plugins/passthrough_demo/forge/modules.yml \
  --build-dir build_passthrough_demo \
  --hls-build-root build_hls_passthrough_demo \
  --output gen-top/design_passthrough_demo/algo_top.v

forge verify generate plugins/passthrough_demo/forge/verify/design.verification.yml
python3 plugins/passthrough_demo/forge/verify/tools/gen_stimulus.py --flow passthrough_xsim --event-id 0
forge verify doctor   plugins/passthrough_demo/forge/verify/design.verification.yml

# Requires Vivado xsim on PATH:
forge verify run plugins/passthrough_demo/forge/verify/passthrough_xsim/verify.flow.yml \
  --plugin passthrough_demo --consumer-root .
```

No `forge hls run` step — there's no HLS module to synthesize.

## Golden events

| Event id | data_in | data_in_valid | Note |
|----------|---------|----------------|------|
| 0 | 0x3A | 1 | normal pass-through |
| 1 | 0x00 | 0 | idle/no input |

Both were verified against a real Vivado xsim run, not just statically checked.
