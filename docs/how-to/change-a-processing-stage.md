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
| Verification to run again | the [targets](verify-from-the-design.md#targets) whose scope covers a change, and replays of traffic that will be recorded again |
| To regenerate | what the last build wrote, and the subsystem tops that contain a change |

`delay_cycles: auto` is sized as `forge build` sizes it (from the HLS reports
under `build: hls_build_root`), so only a change shows as a change.
Numbered copies that change the same way are one line (`csc_*.csp_out->subdet.in_csc_* (52)`),
and lists of instances are ranges (`dt_0..14`). With `--variant`, the
comparison is with that variant's last build.

The report is written to `.forge/reports/impact.md`; `--json` gives it in
`metrics.impact`, with `metrics.rerun` and `metrics.regenerate`. A
misaligned merge makes the result a warning. Then:

```bash
forge build
forge test --target payload,shaper_replay
```
