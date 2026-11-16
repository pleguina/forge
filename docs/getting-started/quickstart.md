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

## 3. Your own firmware

In the repository of a payload you already have, one command writes the only
file FORGE needs:

```bash
forge init --expected data/expected.txt --input data/input.txt
```

```
forge init — /work/my_payload/forge.yml
  ✅ sources: Hog project algo (Top/algo/list)
  ✅ top: payload_top
  ✅ clock: clk
  ✅ reset: rst (active high)
```

It reads where your sources are described (a Hog project, a `.f` compile
list, else the directories holding RTL), the top, the clock and the reset;
nothing is moved and no other file is written. What it cannot tell from the
repository, such as which of two tops you mean, comes back as a question
with the flag that answers it (`--top`, `--clock`, `--reset`,
`--hog-project`, `--filelist`). Without `--expected`, `forge test` still
simulates, and says there was nothing to compare against. Then:

```bash
forge check
forge test
```

[Compare existing firmware](compare-existing-firmware.md) goes on from here.

## 3b. The example plugin

`forge init --example` installs an example plugin, a pass-through design with
its verification files, to show the generation chain working end to end. It
is the starting point for extension authors, not for an existing payload.

```bash
forge init --example my_plugin
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
  ✅ forge.yml written
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
    stage is skipped and everything else still runs, so this command works
    on a machine that has never had an EDA tool installed. Nothing has then
    checked the generated top level, so the result is `WARN`, not `PASS`.
    `forge doctor` tells you what's missing.

Add `--verbose` to see each underlying stage's full output instead of one
line per stage, or `--json` for the machine-readable envelope.

`forge init --example` also writes `forge.yml` (unless the directory already has
one), naming the plugin's design and verification file. From then on, anywhere
under this directory, `forge check`, `forge build`, `forge test` and
`forge report` need no arguments: after you edit the RTL or the golden
data, `forge test` regenerates the top level and simulates it again.

## 4. Read the design back

```bash
forge inspect
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
