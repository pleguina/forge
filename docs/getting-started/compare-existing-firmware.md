# Compare your existing firmware with reference data

The first thing FORGE can do for a payload you already have is simulate it
against the output it should produce. Your top stays your top: nothing is
moved, regenerated or rewritten, and you do not answer any adoption
question first.

## What you need

- The name of your top-level module, and the sources it compiles from: a
  glob under `sources:` or the compile list (`.f`) you already use.
- Its clock and reset port names.
- An input and the output you expect for it, as JSON keyed by interface, or
  as EMPv2/APx pattern files (any format in the
  [support matrix](../reference/support-matrix.md)).

## forge.yml

```yaml title="forge.yml"
project: my_payload
top: payload_top
clock: {port: clk, frequency: 240MHz}
reset: {port: rst_n, active: low}
verification:
  target: existing
  filelist: sim/files.f        # +incdir+, +define+, -f and file paths
  input: data/input.json
  expected: data/expected.json
```

`forge check` confirms the top is found, every port width is known (a width
a define in the compile list sets is evaluated with it) and the clock and
reset are its ports. `forge test` builds a replay testbench around the top,
drives the input interfaces, and compares the outputs:

```
  ✅ existing:payload_top [xsim]: VERIFIED (result 30/30)
  ✅ existing:payload_top [verilator]: VERIFIED (result 30/30)
```

The interfaces come from port names: a `sample` port with `sample_valid` is
a `valid` stream, `_tready` adds backpressure, EMP link signals are an
`emp_lword`. When the names follow no convention, declare them under
`verification.protocols`.

## The result

`forge report` writes `verification_results.md`. Each run is kept under
`.forge/reports/test/runs/`, and a result counts only while the sources, the
headers they include, the data and the verification settings are unchanged;
after an edit it is shown as out of date until `forge test` runs again. In
CI, run `forge test --require-verified`.

## One instance at a time

A payload of many processors is slow to simulate. Name a payload target, and
one for the instance you are working on, replaying what the payload run
recorded at it:

```yaml title="forge.yml"
verification:
  target: existing
  filelist: sim/files.f
  targets:
    payload:
      input: data/input.json
      expected: data/expected.json
    fine:
      scope: instance:u_fine        # labels from your top down
      replay: payload
```

The instance is simulated with the parameters your top gives it, its clock
and reset found through the port map. After an edit, `forge test --target
fine` checks it alone against that traffic; `--event-id` restricts the
payload run, and so the recording, to one event. See
[targets](../how-to/verify-from-the-design.md#targets).

## On the board

The same reference, packaged with its link mapping and the build's identity
(`forge report --package payload`), is what the board plays; `forge test
--capture` compares what it captured with the same scoreboard. See
[compare a board capture](../how-to/compare-a-board-capture.md).

## Then

When you want FORGE to assemble the payload instead, `forge adopt` starts
from the same `forge.yml`; see [bring your own RTL](bring-your-own-rtl.md).
A flow with its own testbench keeps it with `requires_generated_tb: false`
(see [integrate verification](../how-to/integrate-verification.md)).
