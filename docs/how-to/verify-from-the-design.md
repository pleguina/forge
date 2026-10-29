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
| any other data port | `scalar`: a value every cycle |

Names that do not follow these are declared in `forge.yml`; a port left in
no interface is an error naming it:

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
HLS module is checked through the levels that contain it.

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

`--stall N` and `--backpressure N` make `ready_valid` inputs withhold a word,
and `ready_valid` outputs drop ready, in N percent of cycles (`--seed` fixes
the pattern), so a design that loses, repeats or reorders words under flow
control fails.

## Statuses and evidence

| Status | Meaning |
|--------|---------|
| `VERIFIED` | Simulated and every expected word matched |
| `FAILED` | A word did not match, an output went X, or the design did not compile or finish |
| `NOT_VERIFIED_NO_ORACLE` | Simulated cleanly, with nothing to compare its function against |
| `BLOCKED_MISSING_TOOL` | The simulator is not installed |
| `SKIPPED` | The level cannot be simulated (an HLS module, a module without the clock or reset port) |
| `PARTIAL` | Some levels verified, others not |

Each level and simulator writes
`.forge/reports/test/auto/<level>.<backend>.evidence.json`: the sources and
their hashes, the compile key, whether the build was reused, the simulator's
version, the input and expected files with their hashes, the protocol and
checker versions, the seed, and every interface's result rows; and an
`evidence_id` over all of it. `junit.xml` next to them has one case per level
and simulator.

## Compile once, run many

The testbench reads its words at run time, so new data reuses the build. The
build is reused only when its compile key (every source, header, the
testbench, the commands and the simulator's version) is unchanged; any
change rebuilds it.
