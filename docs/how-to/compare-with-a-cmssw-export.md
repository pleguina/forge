# Compare a payload with a CMSSW export

A trigger payload is checked against the emulator that defines it: the
inputs CMSSW saw, packed as the links carry them, and the objects CMSSW
produced. FORGE runs the payload, compares, and names each object that
differs by its event and its fields; the project supplies the one thing only
it knows, how its export maps onto its ports and words.

The worked example is the OMTF payload (Phase-2 Overlap Muon Track Finder):
the full chip, a design FORGE generates from 32 modules and about 500 top
ports, against CMSSW's `L1TMuonOverlapPhase2` emulator. Its adapter and
decoder live in the OMTF firmware repository; this page shows their shape.

## What the project writes

| Piece | What it does | OMTF |
|---|---|---|
| Adapter | turns the export into FORGE's input and expected data | `tools/forge_reference.py`, about 150 lines; it reuses the project's existing XML-to-link-word packer |
| Decoder | names the fields of an output word, `None` for an empty slot | 20 lines, the SAM word layout of CMSSW's `Phase2L1GMT` |
| `forge.yml` | the target, which outputs the reference covers, how to compare | 40 lines, simulation variant included |

Everything else (testbench, simulator invocation, scoreboard, event
bookkeeping, evidence, reports) is FORGE's.

## The data

The adapter writes a dataset of events (see [the data a level
takes](verify-from-the-design.md#data)). Each event is named the way the
export names it, so a failure can be found again in CMSSW:

```json
{"events": [
  {"id": null, "dt_0_cfg64": ["0x..."], "dt_0_cfg_valid": [0, 1, 0], "bx_timing_bx0_global": [0, ..., 1, ...]},
  {"id": "442:1", "dt_0_csp_in": ["0x0", "0x...", ...], "csc_17_csp_in": [...]},
  {"id": null, "dt_0_csp_in": ["0x0", ...]}]}
```

The first entry, with a null `id`, is the board's configuration and the BX
that carries the orbit marker; the last is idle. They belong to no event, so
`--event-id` keeps them. Every event gives each per-cycle input the same
number of words (9: one BX); one that does not is an error naming the event,
so a word lost on one link cannot shift every later event.

The expected file holds the objects CMSSW produced, per event, in the
payload's word format, and the provenance of the export:

```json
{"provenance": {"cmssw_release": "CMSSW_20_0_0", "cmssw_commit": "111315ea...",
                "config": {"file": "L1Trigger/L1TMuonOverlapPhase2/test/firmware_xml/firmware_xml_cfg.py", "sha256": "..."}},
 "events": [{"id": "442:1", "gmt": ["0x..."]}, ...]}
```

## The target

```yaml
variants:
  xsim:                 # configuration words as ports instead of the board's register bus
    remove: {modules: [gmt_tmux_dly, gmt_orbit_dly], topology_groups: [cfg_to_dt, ...]}
    modules:
      dt: {external_in_ports: [csp_in, cfg64, cfg_valid]}
      ...
verification:
  backends: [xsim]
  decoder: forge_utils/verify/forge_decode.py:decode
  xsim:
    elab_libs: [unisims_ver, unimacro_ver, secureip]
    extra_top_modules: [glbl]
    extra_sources: [$XILINX_VIVADO/data/verilog/src/glbl.v]
  streams:
    gmt: gmt_linker_TMUX_Out_*        # 18 time-multiplexed links, one stream
  outputs: [gmt]
  compare: {fields: [pt, eta, phi], order: event}
  drain: 400
  settle: 200
  targets:
    p2:
      variant: xsim
      input: .forge/reference/p2/input.json
      expected: .forge/reference/p2/expected.json
```

`fields` compares what CMSSW defines (pt, eta and phi; the firmware's quality
is not CMSSW's), `order: event` lets the muons of one event come in any
order, and the stream reads the 18 output links in cycle order, so the
comparison does not depend on which link a BX lands on.

## Running it

```bash
python3 -m tools.forge_reference --xml TestEvents_part000.xml --board OMTFp2 --count 150 \
    --out .forge/reference/p2
forge test --target p2              # every event
forge test --target p2 --event-id 442:1
forge report
```

On OMTF, 150 back-to-back events of one board match CMSSW (150 of 150 muons
with the same pt, eta and phi, no other word), as the project's own
full-chip check finds on the same events. A mismatch names the event, the
decoded fields on both sides and the cycle; an object the payload lost is
`missing` in its own event and does not displace the following ones. The
report says where each reference came from: release, commit, configuration
and the export's hash.

## What stays the project's

- Which CMSSW collection is the reference, and which of its fields the
  firmware is expected to reproduce.
- How the export's inputs pack into link words, and the board's
  configuration words.
- The word layout of the outputs (the decoder).

FORGE does not run `cmsRun`: it consumes the export a CMSSW workflow
already writes, with its provenance.
