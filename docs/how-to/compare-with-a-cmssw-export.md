# Compare a payload with a CMSSW export

A trigger payload is checked against the emulator that defines it: the
inputs CMSSW saw, packed as the links carry them, and the objects CMSSW
produced. FORGE runs the payload, compares, and names each object that
differs by its event and its fields; the project supplies the one thing only
it knows, how its export maps onto its ports and words.

## Start from the example

`examples/cmssw_reference` in the repository is a working comparison you
adapt: 100 events of a real CMSSW sample (the OMTF emulator's candidates and
the GMT muons it made of them, with their provenance), a small payload, and
the files that connect them.

```bash
cd examples/cmssw_reference
python3 make_patterns.py   # export -> pattern files, one packet per link and event
forge test
```

For another algorithm, replace four things and keep the rest:

| Replace | With |
|---|---|
| `export/*.json` | your subsystem's collections: `{"provenance": {...}, "events": [{"id": ..., "<collection>": [{field: value, "link": n}]}]}` |
| `codecs/*.yml` | where each field sits in your input and output link words |
| `rtl/` | your algorithm, its top in `forge.yml` |
| `forge.yml` `links`, `compare.fields` | the links your interfaces are on, the fields CMSSW defines |

A codec is a field layout; the same file packs the export into the input
and expected pattern files (`forge.integration.objects.write_patterns`) and
reads the payload's output words back (`verification.codecs`), so the two
cannot disagree:

```yaml
width: 64
fields:
  valid:  {low: 0, bits: 1, value: 1}
  charge: {low: 1, bits: 1}
  pt:     {low: 2, bits: 13}
  phi:    {low: 15, bits: 13, signed: true}
  d0:     {low: 41, bits: 7, default: 0}    # the export gives none
```

An object missing a field without a `default` is an error, so `pT` in the
export is not packed as `pt = 0`. A layout shared by packing and reading
cannot catch its own mistake: check it once against a word written down
independently (the subsystem's own packer, or a word from its firmware
documentation), as `forge/tests/test_integration_objects.py` does for the
SAMuon word.

```yaml
verification:
  input:    {path: data/input.txt, format: empv2, events: data/input.events.json}
  expected: {path: data/expected.txt, format: empv2, events: data/expected.events.json,
             provenance: data/provenance.json}
  links: {l0: 0, q0: 0}
  codecs: {"q*": codecs/samuon.yml}
  compare: {fields: [pt, eta, phi, charge, qual], order: event}
```

A payload bug is reported per event with the decoded fields on both sides;
`--event-id` runs one event; a packet missing on one link is an error naming
the event and link.

## Pattern files CMSSW already writes

A subsystem whose emulator writes its board patterns with
`L1Trigger/DemonstratorTools`' `BoardDataWriter` needs no export and no
codec for packing: FORGE reads those EMPv2 files as they are. What the files
do not say is which event each packet is. `BoardDataWriter` places events by
fixed rules (TMUX rotation over a channel's indices, offset, gap, a new file
when the buffer would overrun), so `forge/integration/cmssw_sidecar.py`
recomputes the placement from the writer's settings and the order the job
processed the events. It needs only Python 3, so it runs in the CMSSW
environment, after the job:

```bash
python3 cmssw_sidecar.py --writer writer.json --log cmsRun.log \
    --config my_cfg.py --provenance sample.json
```

`writer.json` repeats the writer's arguments (`path`, `ext`,
`frames_per_bx`, `board_tmux`, `max_frames_per_file`, and per channel
`tmux`, `gap`, `offset`, `indices`). For each file it writes
`<file>.events.json` (each event's run, lumi and event number and its frames
on every channel) and `<file>.provenance.json` (CMSSW release and commit
from the environment, the configuration's checksum, and what
`sample.json` adds: GlobalTag, firmware configuration, LUT hashes). Point the
data spec at them:

```yaml
verification:
  expected: {path: patterns/tx_0.txt, format: empv2, events: patterns/tx_0.txt.events.json,
             provenance: patterns/tx_0.txt.provenance.json}
```

With frames in the events file, FORGE takes each event's packets from the
frames the writer gave it rather than grouping packets that overlap in time,
so time-multiplexed events on different links keep their identity, and a
file that does not match the layout (an event missing, another writer
setting) is refused. The event order comes from the job's log, which is the
writer's order for a job on one stream; with several streams, give the order
with `--events` instead. Each file the writer splits off is its own
reference: name the one a target plays.

## A full-chip payload: OMTF

The OMTF payload (Phase-2 Overlap Muon Track Finder) is the same comparison
at full scale: the whole chip, a design FORGE generates from 32 modules and
about 500 top ports, against CMSSW's `L1TMuonOverlapPhase2` emulator. Its
inputs are per-cycle port values rather than pattern files, and its outputs
are 18 time-multiplexed links. Its adapter and decoder live in the OMTF
firmware repository; the rest of this page shows their shape.

### What the project writes

| Piece | What it does | OMTF |
|---|---|---|
| Adapter | turns the export into FORGE's input and expected data | `tools/forge_reference.py`, about 150 lines; it reuses the project's existing XML-to-link-word packer |
| Decoder | names the fields of an output word, `None` for an empty slot | 20 lines, the SAM word layout of CMSSW's `Phase2L1GMT` |
| `forge.yml` | the target, which outputs the reference covers, how to compare | 40 lines, simulation variant included |

Everything else (testbench, simulator invocation, scoreboard, event
bookkeeping, evidence, reports) is FORGE's.

### The data

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

### The target

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

### Running it

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
