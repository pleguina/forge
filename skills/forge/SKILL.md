---
name: forge
description: >-
  Use FORGE (the `forge` CLI) on an FPGA firmware repository: simulate an
  existing top level against reference data, adopt a Verilog/SystemVerilog/
  VHDL/HLS repository and generate its structural top level, read and settle
  what FORGE reports, and debug a failed verification. Use when the
  repository has a forge.yml, when asked to verify or compare FPGA firmware
  with expected output (JSON, EMPv2 or APx pattern files), or when asked to
  assemble RTL/HLS modules into one top level.
---

# Using FORGE

FORGE reads an FPGA repository as it is, records every design decision in one
file, `forge.yml`, and from it generates a structural top level, simulates it
(XSim, Verilator) against expected output, and reports what is verified.

It never guesses. Where more than one reading of the sources is valid it stops
and asks. Those questions are the user's to answer, not yours: report them
with their options and let the user choose, unless the user has already told
you the answer.

## Rules

1. **Always pass `--json`** and read the envelope, not the human text:

   ```json
   {"schema_version": "0.1.0", "status": "pass|warn|fail|error",
    "diagnostics": [{"code": "ATG037", "severity": "warning", "message": "...",
                     "action": "...", "context": {"options": ["..."]}}],
    "artifacts": ["files written"], "metrics": {}, "next_actions": ["..."]}
   ```

   Exit codes: `0` pass (or warn), `1` fail (or warn with `--strict`),
   `2` usage error or internal error. A `warn` exits 0: check `status`, not
   only the exit code. Diagnostic messages go to stdout inside the envelope;
   stderr is progress and tool output.

2. **Edit `forge.yml`, never `.forge/`.** `.forge/` is rewritten by every
   command from `forge.yml` and the sources; an edit there is lost. Do not
   move or rewrite the user's RTL to suit FORGE.

3. **Do not answer FORGE's questions on the user's behalf.** A diagnostic
   whose `context.options` lists alternatives (ambiguous consumer, which
   clock, which top, opaque or not) is a decision about intent. Show it to
   the user with the options. The same goes for module latencies and port
   widths the source does not state (`ATG051`): ask, do not infer them from
   comments or names.

4. **Only report VERIFIED when FORGE says VERIFIED.** `NOT_VERIFIED_NO_ORACLE`
   (no expected output), `BLOCKED_MISSING_TOOL` (no simulator), `SKIPPED`,
   `PARTIAL` and `OUT_OF_DATE` are not passes. Use
   `forge test --require-verified` when the answer must be binary.

5. **Do not edit expected data to make a test pass.** A mismatch is a finding
   about the design or the reference; report it.

6. **`forge fix` previews by default.** It only applies repairs proven by the
   sources. Show the preview; run `forge fix --apply` when the user agrees or
   asked for fixes.

7. Avoid interactive commands (`forge adopt --interactive`); edit `forge.yml`
   or use `forge connect <producer> <consumer>` instead.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install "git+https://github.com/pleguina/forge.git@<commit-sha>#subdirectory=forge"
forge doctor --json        # which simulators and tools are on PATH
```

There is no PyPI package; pin a commit. `forge doctor` reporting a missing
`xvlog`/`verilator` means `forge test` will return `BLOCKED_MISSING_TOOL` for
that backend; restrict to the other with `--backend`.

## Pick the task

| The user wants | Path |
|---|---|
| To check an existing top level against reference output, unchanged | [A. Compare existing firmware](#a-compare-existing-firmware) |
| FORGE to assemble modules into a generated top level | [B. Adopt and generate](#b-adopt-and-generate) |
| A project that already has `forge.yml` | Run `forge check --json` and follow [The loop](#the-loop) |

### A. Compare existing firmware

No adoption, nothing generated. Write `forge.yml`:

```yaml
project: my_payload
top: payload_top                 # the module to simulate
clock: {port: clk, frequency: 240MHz}
reset: {port: rst_n, active: low}
verification:
  target: existing
  filelist: sim/files.f          # or sources: {rtl: [rtl/*.sv]}
  input: data/input.json
  expected: data/expected.json
```

The `.f` file may use `+incdir+`, `+define+`, `-I`, `-D`, `-f`.
`verification.defines: {NAME: value}` and `verification.include_dirs: [...]`
add to it. Then:

```bash
forge check --json                     # top found, widths known, clock/reset are ports
forge test --plan --json               # what will be driven and compared; no simulation
forge test --json                      # simulate and compare
forge report --json                    # verification_results.md under .forge/reports/
```

### B. Adopt and generate

```bash
forge adopt --json .        # writes forge.yml and .forge/; touches nothing else
forge check --json
```

`adopt` writes only what the sources prove. Each open question is an `ATG037`
(or `ATG042` for an unsupported module) diagnostic with `action` (the exact
`forge.yml` edit) and `context.options`. After the user answers, write the
edit and re-run `forge adopt --json .`; it reads `forge.yml` and leaves it as
it is, regenerating only `.forge/`. Then `forge build --json` writes `.forge/generated/algo_top.v`.

## The loop

```bash
forge check --json     # metrics.sections: one state per area; next_actions in order
forge next --json      # metrics.recommended_action: {description, command, safe, auto_fixable}
```

Run the recommended command when `safe` is true and it does not need a
decision; otherwise bring the `next_actions` entry to the user. Repeat until
`forge check` has no `FAIL` section and verification is `VERIFIED`.

Section states: `PASS`, `WARN`, `PARTIAL`, `FAIL`, `NOT CONFIGURED`.

## forge.yml

```yaml
project: name
part: xcvu9p-flga2104-2L-e
top: my_top                          # existing target, or the top when ambiguous
sources: {rtl: [rtl/*.v], hls: [hls/*.cpp]}
clock: {port: clk, frequency: 240MHz}  # or period_ns: 4.166; sets the testbench clock
reset: {port: rst_n, active: low}
connections:                         # answers to "which consumer" questions
  - {from: producer.port, to: consumer.port}
modules:
  decimator:
    latency: 2                       # or {kind: bounded, min_cycles: 1, max_cycles: 3}
    widths: {lookup_addr: 4}         # widths the source cannot evaluate (ATG051)
    contract: contracts/decimator.interface.yaml   # a contract you maintain
  vendor_ip: {management: opaque}    # integrate structurally, clocks routed out
verification:
  input: data/input.json             # or {path: ..., format: empv2, events: events.json}
  expected: data/expected.json
  levels: [design]                   # design, modules (module:<name>)
  backends: [xsim, verilator]
  compare: {mode: transaction}       # exact_cycle | fixed_latency | latency_window
  # compare: {mode: latency_window, latency: {min: 3, max: 5}, tolerance: 0}
  protocols:                         # only for ports that follow no naming convention
    hit: {protocol: valid, data: [hit_lo, hit_hi], valid: hit_strobe}
  links: {l0: 0, q: 1}               # interface -> link, for pattern files
  association: {max_skew: 0, missing: error}   # grouping pattern-file packets into events
  decoder: tools/decode.py:decode    # decode(interface, word) -> {field: value}
  targets:                           # instead of input/expected: named things to check
    payload: {input: data/in.json, expected: data/exp.json}   # scope: design
    sorter: {scope: "instance:sorter_3", replay: payload}     # traffic payload recorded
    front: {scope: subsystem, instances: [unpacker, sorter], replay: payload}
    packer: {scope: "module:packer", input: p_in.json, expected: p_out.json, required: false}
```

Only required targets (default) decide the status and `--require-verified`.
Without `targets`, the design is required and a module only when it has its
own expected output.

A project with a hand-written design keeps it: `design: path/design.yml`,
`registry: path/modules.yml`, `verification.config: path/design.verification.yml`
(plugin flows; `forge test` then runs those flows).

## Data

JSON keyed by interface name, one entry per transaction, in order:

```json
{"decimator_sample_in": [4660, "0x1a2b", 7]}
```

A value is a number or a mapping from data port to value. Interfaces are
found from port names: `x` + `x_valid` is `valid`; adding `x_ready` makes it
`ready_valid`; `x_tdata/x_tvalid/x_tready` is AXI4-Stream; `x_data` + `x_valid`
+ two of `x_start/x_last/x_start_of_orbit/x_strobe` is `emp_lword`;
`ap_start/ap_done` is an HLS call. Run `forge test --plan --json` to see the
interfaces and their names before writing data.

Pattern files (`empv2`, `apx`, `apx-2021`) are read directly with `links:`.
An `events:` file next to them (a JSON list, one entry per event:
`{"run":..,"lumi":..,"event":..,"bx":..}` or `{"id":..}`) names each event in
mismatch rows.

## Reading results

`forge test --json`: `metrics.levels` maps `level[backend]` to a status;
`metrics.targets` gives each target's `scope`, `required`, `reference`
(`functional`: an expected output was given; `derived`: recorded traffic, a
regression reference only; `none`) and `status`; `metrics.verification` is
the overall status of the required ones. Per level and simulator the
evidence is `.forge/reports/test/auto/<level>.<backend>.evidence.json`:

```json
"interfaces": [{"interface": "packer_packet_out", "status": "FAILED",
  "expected": 20, "observed": 20, "matched": 19,
  "rows": [{"kind": "mismatch", "index": 3, "cycle": 11,
            "expected": "0x379d7", "observed": "0x379d6"}]}]
```

Row kinds: `mismatch`, `missing`, `extra`, `duplicate`, `out_of_order`,
`wrong_cycle`, `latency`, `unknown` (X/Z). With events, rows carry the event
name; with a decoder, `expected_fields`/`observed_fields`.

| Status | Meaning |
|---|---|
| `VERIFIED` | simulated, every expected word matched |
| `FAILED` | a mismatch, an X/Z output, or the build/simulation failed |
| `NOT_VERIFIED_NO_ORACLE` | ran cleanly, no expected output to compare with |
| `BLOCKED_MISSING_TOOL` | simulator not installed |
| `SKIPPED` | level cannot be simulated |
| `PARTIAL` | some required levels verified, others not |
| `OUT_OF_DATE` | a source, included header, data file or verification setting changed since the run |

Every run is kept under `.forge/reports/test/runs/<id>/`. `forge report --json`
recomputes staleness; re-run `forge test` when it says out of date.

## Debugging

| Symptom | Do |
|---|---|
| A diagnostic code you don't know | `forge explain <CODE> --json` |
| Why two ports are (not) connected | `forge explain connection:a.q->b.d` / `forge explain <module>.<port>` |
| Why a port reads as clock or reset | `forge explain clock:<port>` / `reset:<port>` |
| Whether a generated file is current | `forge explain artifact:.forge/generated/algo_top.v` |
| `ATG051` unresolved width | ask for the width; set `modules.<m>.widths` (or a define for an existing target) |
| A port in no interface | `forge test --plan` names it; declare it under `verification.protocols` |
| FAILED, compile or simulation error | the evidence's `reason` names its `compile.log` or `sim.log` |
| FAILED, rows | report the first rows (interface, index, cycle, expected, observed, event) to the user |
| Only design-level reference | `forge test --record-modules` records each instance's I/O as a derived module reference |
| One instance or event is wrong in a slow payload | declare an `instance:` target with `replay: <design target>`; `forge test --target <design> --event-id N`, then `forge test --target <instance>` after each edit |

## Commands

| Command | Use |
|---|---|
| `forge doctor --json` | installation and simulators |
| `forge adopt --json [path]` | create `forge.yml` from an existing repository (`--dry-run` to preview) |
| `forge check --json [--strict]` | project status, every blocker with its action |
| `forge next --json` | the single most urgent step |
| `forge build --json [--dry-run]` | generate the top level into `.forge/generated/` |
| `forge test --json [--plan] [--target T] [--event-list E] [--backend B] [--require-verified]` | simulate and compare |
| `forge test --input F --expected F --json` | one-off data without editing `forge.yml` |
| `forge report --json` | collect results into `.forge/reports/` |
| `forge inspect --json [--emit-ir F]` | the resolved design (canonical IR) |
| `forge inspect --impact [OLD_IR] --json` | what changed since the last build (or a snapshot), what it affects, misaligned merges, targets to rerun, files to regenerate |
| `forge explain <target> --json` | why FORGE decided (or refused to decide) something |
| `forge fix --json [--apply]` | source-proven repairs, previewed as a diff |
| `forge connect a.port b.port --json` | record one connection in `forge.yml` |
| `forge migrate --json [--apply]` | bring project files to this FORGE's schemas |

Run from anywhere inside the project; commands find `forge.yml` upwards like
`git` finds `.git`.
