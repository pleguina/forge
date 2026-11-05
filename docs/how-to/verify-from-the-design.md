# Verify from the design

`forge test` verifies an adopted design from its input and expected output
alone: no plugin, no stimulus script, no testbench, no flow file. FORGE finds
the design's interfaces from its ports, generates one testbench, compiles it
once per simulator, runs every transaction in one simulation, and compares
what comes out with what was expected.

```bash
forge adopt .
forge test --input input.json --expected expected.json
```

```
  ✅ design [xsim]: VERIFIED (packer_packet_out 20/20)
  ✅ design [verilator]: VERIFIED (packer_packet_out 20/20)
  ⚠️  module:decimator [xsim]: NOT_VERIFIED_NO_ORACLE (decimated 0 words)
  ...
```

A project whose `forge.yml` names a `verification.config` keeps running that
plugin's flows ([Integrate verification](integrate-verification.md)); passing
`--input` uses this path instead.

## What FORGE infers, and what it cannot

`forge test --plan` prints the plan without simulating: each level, its
clock and reset, its interfaces and their protocols, the checks it will run.

| From the ports | Interface |
|----------------|-----------|
| `x` and `x_valid` | `valid`: a word per cycle with valid high |
| `x`, `x_valid` and `x_ready` (ready the other way) | `ready_valid` |
| `x_tdata`, `x_tvalid`, `x_tready` | `ready_valid` (AXI4-Stream) |
| `x_data` with `x_valid` and two of `x_start`, `x_last`, `x_start_of_orbit`, `x_strobe` | `emp_lword` |
| `x` and HLS's `x_ap_vld` | `valid` |
| HLS block control `ap_start`, `ap_ready`, `ap_done` (`ap_ctrl_hs`) | the scalar inputs are one `call` (`ready_valid`: held with `ap_start` until `ap_ready`), the scalar outputs its `result` (`valid` at `ap_done`) |
| any other data port, or a valid or ready that pairs with no data port | `scalar`: a value every cycle |

Names that do not follow these are declared in `forge.yml`:

```yaml
verification:
  protocols:
    hit: {protocol: valid, data: [hit_lo, hit_hi], valid: hit_strobe}
```

What the ports cannot give is the design's function. A level is checked
against an expected output only where one is given; every level is still
compiled, reset, run and checked for X and Z on its outputs, and reports
`NOT_VERIFIED_NO_ORACLE` without one, never a pass.

## Levels

| Level | What |
|-------|------|
| `design` | The top level `forge build` generates (or your own design's), with `verification.input`/`expected` or `--input`/`--expected` |
| `module:<name>` | Each RTL module on its own, with `forge.yml`'s clock and reset ports, and `verification.modules.<name>.input`/`expected` when given |

`--level design` or `verification.levels: [design]` runs one level only. An
HLS module is checked through the levels that contain it. The design's
result decides the project's status; a module's only when it has an expected
output of its own, so a design comparison that passed is VERIFIED even while
the modules have no reference:

```
Required: design VERIFIED (reference comparison); not required: module:decimator
NOT_VERIFIED_NO_ORACLE (no reference supplied), ...
```

When the only reference is at the design level, `--record-modules` records,
during the design-level run, what every module instance received and
produced, under `.forge/verify/design/<backend>/module_traces/<instance>/`
(`input.json`, `expected.json`). Naming them as a module's
`verification.modules.<name>.input`/`expected` checks that module against
them, and its evidence carries `expected.derived_from`: the run they were
recorded from. They are a regression reference for a module, taken from the
design as it behaved, not an independent statement of what it should do.

## Data

A JSON input names each input interface and its transactions, in order; an
expected output the same for outputs:

```json
{"decimator_sample_in": [4660, "0x1a2b", 7]}
```

A value is a number, or a mapping from data port to value for an interface
of several ports. An `emp_lword` transaction is
`{"data": ..., "valid": 1, "start": 1, "last": 0, "start_of_orbit": 0, "strobe": 1}`.
An expected word may be `{"value": ..., "cycle": N, "source": K}` for the
timed comparison modes.

EMP (`empv2`) and APx (`apx`, `apx-2021`) pattern files are read directly,
with the link each interface is on:

```yaml
verification:
  input:    {path: patterns/source.txt, format: empv2}
  expected: {path: patterns/sink.txt, format: empv2}
  links: {l0: 0, q: 1}
```

An `emp_lword` interface takes every frame of its link; any other interface
takes the link's valid words as its transactions.

A dataset of events (`{"events": [...]}`, each event naming its interfaces)
tags every word with its event. An entry whose `id` is null holds words of no
event: configuration written before the first event, idle words after the
last. A `scalar` input takes a word every cycle, so every entry spans the same
cycles on every scalar input: one the entry gives fewer words, or none, holds
its last word, and an event whose scalar inputs give different numbers of
words is an error naming it, since one word missing on one input would shift
every later event on that input alone. A `provenance` mapping in the file
(software release, commit, configuration, golden set) is carried into the
evidence and the report.

```json
{"provenance": {"cmssw_release": "CMSSW_20_0_0", "cmssw_commit": "111315ea"},
 "events": [
   {"id": null, "dt_0_cfg64": ["0x1f"], "dt_0_cfg_valid": [0, 1, 0], "dt_0_csp_in": [0, 0, 0]},
   {"id": "442:1", "dt_0_csp_in": ["0x0", "0x2b5", "0x0"]},
   {"id": null, "dt_0_csp_in": [0, 0, 0]}]}
```

## Comparison

```yaml
verification:
  compare:
    mode: latency_window      # transaction | exact_cycle | fixed_latency | latency_window
    latency: {min: 3, max: 5}
    tolerance: 0
```

| Mode | Checks |
|------|--------|
| `transaction` | the k-th output word equals the k-th expected one; idle cycles do not count |
| `exact_cycle` | and each expected word with a `cycle` arrives in that cycle |
| `fixed_latency`, `latency_window` | and each word leaves `latency` cycles after its input transaction (`source`, by default its own index) |

Every result row names what went wrong: `mismatch`, `missing`, `extra`,
`duplicate`, `out_of_order`, `wrong_cycle`, `latency`, `unknown` (X or Z).
`tolerance` allows a numeric difference in the data only.

A reference often defines fewer bits than the firmware writes, and fixes no
order among the objects of one event. `fields` compares words by the fields
`verification.decoder` reads from them, and a word the decoder reads as no
object (`None`, an empty slot) is not a transaction on either side; `order:
event` allows any order within an event (`any`: over the whole interface):

```yaml
verification:
  decoder: tools/decode.py:decode      # decode(interface, word) -> {field: value} or None
  compare: {fields: [pt, eta, phi], order: event}
```

A field layout can stand in for the decoder: `verification.codecs` maps
an output (or a glob of outputs) to a codec file, whose fields name the bits
of each word; a word with no data bits set is no object. The same codec packs
a reference export into pattern files
([compare with a CMSSW export](compare-with-a-cmssw-export.md)). A data spec's
`provenance:` names a JSON file of where the data came from, for pattern
files, which cannot carry one.

An observed word belongs to the event an alignment of the observed and
expected sequences puts it against, so a word missing or extra in one event
is reported there and does not displace the later events. A mismatch pairs
the expected word with the observed one that shares most fields with it.

`--stall N` and `--backpressure N` make `ready_valid` inputs withhold a word,
and `ready_valid` outputs drop ready, in N percent of cycles (`--seed` fixes
the pattern), so a design that loses, repeats or reorders words under flow
control fails.

## Time-multiplexed and free-running payloads

A trigger payload often writes one stream of objects over several links in
turn and outputs a word every cycle. `streams` reads several outputs as one,
in cycle order (by name at the same cycle); a target's `outputs` names the
outputs its reference covers, and the others are traced but not compared;
`drain` is the number of cycles simulated after the last input, and `settle`
the cycles after reset in which X on an output is not an error (a pipeline
whose data registers have no reset fills with X until its first input
reaches the output):

```yaml
verification:
  streams:
    gmt: gmt_linker_TMUX_Out_*       # names or globs
  outputs: [gmt]                     # every design target; a target can name its own
  drain: 400
  settle: 200
```

A `scalar` output is recorded every cycle and is not activity: a design whose
outputs all change every cycle ends `drain` cycles after its last input.

## Vendor simulation libraries

Sources generated by vendor tools need their simulation libraries. For XSim,
the same keys as a plugin flow's `xsim:`; `.dat` files next to a source (HLS
ROM contents) are linked into the run directory:

```yaml
verification:
  xsim:
    elab_libs: [unisims_ver, unimacro_ver, secureip]
    extra_top_modules: [glbl]
    extra_sources: [$XILINX_VIVADO/data/verilog/src/glbl.v]
```

## Targets

When the question is narrower or wider than "the design and every module",
name what to check under `verification.targets`. Each target has a scope,
its data and comparison, and whether its result is required:

```yaml
verification:
  targets:
    payload:                          # scope: design (the default)
      input: data/input.json
      expected: data/expected.json
    sorter:
      scope: instance:sorter_3        # one instance, with its own parameters
      replay: payload                 # the traffic payload's run recorded at it
    front_end:
      scope: subsystem
      instances: [unpacker, sorter]   # instances, or a block for all of its instances
      replay: payload
    packer_alone:
      scope: module:packer            # a module on its own, with its block's parameters
      input: data/packer_in.json
      expected: data/packer_out.json
      required: false                 # reported, but does not decide the status
```

| Scope | Simulates |
|-------|-----------|
| `design` | the generated top (or, with `target: existing`, your own) |
| `module:<name>` | one module on its own, with its block's parameters |
| `instance:<path>` | the module of one instance with that instance's parameters, clock and reset; in your own top, `<path>` is the labels from the top down (`u_proc.u_sorter`) |
| `subsystem` | a top generated from the design with only `instances` and the connections among them; every port a connection to the rest reached becomes a port. A subsystem whose connections come out different from the design's is refused, naming them |

Every target takes `input`, `expected`, `compare`, `gap`, `protocols`,
`backends` and `required` (default `true`). Only the required targets decide
the status `forge test` exits with, `--require-verified` included; the others
are reported as not required.

### Replay a failure without the payload

`replay: <design target>` drives an instance or subsystem target with the
traffic that design target's run recorded at its boundary, and expects what
it recorded coming out. The design target records it on every run once a
target replays it (`record: true` records every instance). After you change
the module, `forge test --target sorter` re-checks it against that traffic in
a fraction of the payload's time; its result is a regression reference
(`reference: derived`), not an independent one, and goes out of date when
the payload's run records new traffic.

`--event-id` or `--event-list` (names or positions: `7`, `2,10-12`,
`run1:5:42@bx3`) runs only those events of the data, which must name its
events (pattern files, an `events:` file, or a dataset JSON with `events`).
Run the design target on the failing event, then replay the instance on
what it recorded:

```bash
forge test --target payload --event-id 7
forge test --target sorter
```

A run of some events is kept under `.forge/reports/test/runs/` and does not
replace the latest results, which stay those of every event.

## Statuses and evidence

| Status | Meaning |
|--------|---------|
| `VERIFIED` | Simulated and every expected word matched |
| `FAILED` | A word did not match, an output went X, or the design did not compile or finish |
| `NOT_VERIFIED_NO_ORACLE` | Simulated cleanly, with nothing to compare its function against |
| `BLOCKED_MISSING_TOOL` | The simulator is not installed |
| `SKIPPED` | The level cannot be simulated (an HLS module, a module without the clock or reset port) |
| `PARTIAL` | Some required levels verified, others not |

Each level and simulator writes
`.forge/reports/test/auto/<level>.<backend>.evidence.json`: the sources and
their hashes, the compile key, whether the build was reused, the simulator's
version, the input and expected files with their hashes, the protocol and
checker versions, the seed, and every interface's result rows; and an
`evidence_id` over all of it, with the target's `scope`, whether it is
`required`, and its `reference`: `functional` (an expected output you gave),
`derived` (recorded traffic) or `none`. `junit.xml` next to them has one case
per level and simulator.

## Compile once, run many

The testbench reads its words at run time, so new data reuses the build. The
build is reused only when its compile key (every source, header, the
testbench, the commands and the simulator's version) is unchanged; any
change rebuilds it.
