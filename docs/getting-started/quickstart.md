# Five-Minute Quickstart

This walks through the exact command sequence FORGE's CI runs to prove a
fresh install works — [`ci/quickstart_commands.sh`](https://github.com/pleguina/forge)
(also sourced by `ci/fresh_user_check.sh`), so what you run here is not a
simplified retelling, it's the same commands.

## 1. Install

```bash
pip install -e "forge[parser]"
```

The `parser` extra enables structured RTL port parsing via pyverilog. It's
optional — a regex fallback is used without it (see
[installation](installation.md)).

## 2. Check the install

```bash
forge doctor
```

`forge doctor` is read-only and safe to run at any time. It reports the
FORGE version, the Python version, every simulation and synthesis tool it
can find on `PATH`, and the resolved location of each framework resource.
Optional tools are reported as warnings, not failures — a clean install
with no EDA tools installed still passes.

## 3. Scaffold and run a plugin

```bash
forge init my_plugin
```

This is the whole loop in one command. It scaffolds the topology side
(`design.yml`, `modules.yml`, an interface contract, an RTL stub) and the
verification side (`design.verification.yml`, stimulus tooling, a golden
dataset), then validates the design, generates the structural top level,
simulates it, and writes a report bundle:

```
forge init — scaffolding 'my_plugin'
  ✅ topology scaffold
  ✅ verification scaffold
  ✅ design validated
  ✅ top level generated (algo_top.v)
  ✅ testbench prepared
  ✅ simulation passed (my_plugin_xsim)
  ✅ report written
✅ status: PASS
```

Open `plugins/my_plugin/report/dashboard.html` — it has the generated
topology, an interactive explorer, the static latency check, and the
simulation result.

!!! note "No simulator installed?"
    The simulation stage needs Vivado xsim on `PATH`. Without it that one
    stage is skipped with a note and everything else still runs, so this
    command works on a machine that has never had an EDA tool installed.
    `forge doctor` tells you what's missing.

Add `--verbose` to see each underlying stage's full output instead of one
line per stage, or `--json` for the machine-readable envelope.

## 4. Read the design back

```bash
forge inspect plugins/my_plugin/forge/designs/design.yml \
    --contracts-from plugins/my_plugin/forge/modules.yml
```

`forge inspect` resolves the design into FORGE's canonical IR and prints
what it found — modules, instances, connections, clock and reset domains,
any diagnostics, and how much of the design is contract-driven rather than
matched heuristically. It never writes anything unless you ask it to with
`--emit-ir`.

## What just happened

- `doctor` gave you an honest, specific status of your toolchain rather
  than a generic pass/fail.
- `init` scaffolded a plugin under a real layout (`forge/…` capsule plus
  `algo/`), matching the one `plugins/passthrough_demo/` and
  `plugins/trigger_demo/` both use — then took it all the way to a
  simulated, reported design without asking you to edit a file first.
- `inspect` read that design back through the same IR every other FORGE
  command uses, so what it printed is what the generator saw.

## Next

- [The golden-path tutorial](../tutorials/golden-path.md) — the same
  ground at a slower pace, using `passthrough_demo` as the running
  example, including the individual stage commands `init` runs for you.
- [Authoring topology contracts](../how-to/author-topology-contracts.md)
  — how to write the `design.yml`/`modules.yml`/interface-contract files a
  real plugin needs once you replace the scaffolded stub.
- [Integrating verification](../how-to/integrate-verification.md) — adding
  verification to RTL you already have, rather than scaffolding fresh.
