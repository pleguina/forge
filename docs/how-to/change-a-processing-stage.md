# Change a processing stage

Replacing one implementation, changing a stage's latency or adding a
register on a link touches more of a large payload than the edit itself.
`forge inspect --impact` says what, by the names in your design, before you
rebuild:

```bash
forge inspect --impact          # against the last `forge build`
forge inspect --impact old.json # against any snapshot `forge inspect --emit-ir` wrote
```

```
## Modules

- **shaper** (rtl/shaper.v): latency: 1 cycles -> 2 cycles

## Sources changed since the build

- **packer**: rtl/packer.v

## What it affects

- Changed: packer, shaper
- Downstream of them: formatter

## Merges whose inputs are no longer aligned

- **merger**: the branch from shaper changed timing, the one from bypass did not

## Verification to run again

- `payload`: the design changed
- `shaper_replay`: instance shaper changed

## To regenerate

- .forge/generated/algo_top.v
- ...
```

| Section | From |
|---|---|
| Modules | ports added, removed or resized, parameters, declared latency, kind, top, interface protocols, source files |
| Sources changed since the build | files whose content differs from what the last build's provenance recorded, even when no port changed |
| Instances | added, removed, or now of another module or clock/reset domain; an input left without its driver (generation ties it off) |
| Connections | added or removed, a width at either end, pipeline registers, delays and synchronizers on them |
| What it affects | the instances that behave or connect differently, and everything they feed |
| Merges | every instance fed by several producers where the timing of some branches changed and of others did not: the places a latency change has to be balanced |
| Merges realigned | merges whose every branch changed because a `delay_cycles: auto` delay into them was resized: the delay and its old and new depth. Every signal on the connection (data, valid, event tags) moves by the same delay |
| Timing not analysed | changed or downstream instances whose latency is undeclared, bounded, elastic or opaque: alignment through them is unknown, not assumed |
| Verification to run again | the [targets](verify-from-the-design.md#targets) whose scope covers a change, and replays of traffic that will be recorded again |
| To regenerate | what the last build wrote, and the subsystem tops that contain a change |

`delay_cycles: auto` is sized as `forge build` sizes it (from the HLS reports
under `build: hls_build_root`), so only a change shows as a change.
Numbered copies that change the same way are one line (`csc_*.csp_out->subdet.in_csc_* (52)`),
and lists of instances are ranges (`dt_0..14`). With `--variant`, the
comparison is with that variant's last build.

A slower stage before a merge is balanced only where you allowed it: with
`delay_cycles: auto` on the other branch, `forge build` resizes the delay
(5 to 7 cycles on one branch turns a 4-cycle delay on the other into 6, on
the data and its valid alike). A delay you fixed yourself, or a merge left
without one, stops the build when the declared latencies no longer line up:

```
❌ Merge inputs out of alignment at the declared latencies:
   • merge: inputs differ by 2 cycle(s) (exact_cycle); Insert signal_delay DEPTH=2 on the path from 'fast' into 'merge'
```

A merge that does not need its inputs on the same cycle says so with its
`input_alignment` (`window` or `framed`). An unknown latency on a branch is
reported by `forge dev analyze latency-check`, not guessed. All of this is
alignment at the declared latencies (contracts, HLS reports): it says
nothing about post-route timing.

The report is written to `.forge/reports/impact.md`; `--json` gives it in
`metrics.impact`, with `metrics.rerun` and `metrics.regenerate`. A
misaligned merge makes the result a warning. Then:

```bash
forge build
forge test --target payload,shaper_replay
```

The targets listed cover the change as far as the static graph sees it.
They are the ones to run first; a release still runs every required target.
