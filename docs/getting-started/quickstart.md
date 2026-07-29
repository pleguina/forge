# Five-Minute Quickstart

This walks through the exact command sequence FORGE's CI runs to prove a
fresh install works — [`ci/quickstart_commands.sh`](https://github.com/<org>/forge)
(also sourced by `ci/fresh_user_check.sh`), so what you run here is not a
simplified retelling, it's the same commands.

## 1. Install and confirm the CLI works

```bash
pip install -e "forge[parser]"
forge --help
```

`forge --help` lists every command group (`core`, `topgen`, `hls`,
`verify`, `analyze`, `framework`) and standalone command (`doctor`,
`inspect`, `build`, `test`, `report`, `init`). If it prints without error,
the install is healthy.

## 2. Scaffold a new plugin

```bash
forge verify init-plugin my_plugin --plugins-root <plugins_root>
```

This creates `<plugins_root>/my_plugin/forge/verify/` with a starter
`design.verification.yml`, `tools/bootstrap.py`, `tools/gen_stimulus.py`,
and a golden XML dataset — everything the next stage needs. It only
scaffolds the *verification* side of a plugin (`forge/verify/`); pairing
it with a real topology (`design.yml`/`modules.yml`) and RTL/HLS source is
covered in the [golden-path tutorial](../tutorials/golden-path.md).

## 3. Health-check it

```bash
forge verify doctor <plugins_root>/my_plugin/forge/verify/design.verification.yml
```

`doctor` is read-only and safe to run at any time (`--json` gives
machine-readable output for scripting). **A freshly-scaffolded plugin
reports `FAIL` here — this is expected, not a broken install.** `doctor`
is telling you what `forge verify generate` still needs to create
(`verify.flow.yml`, testbenches, wave scripts), not reporting that
something is wrong with FORGE itself. Re-run `doctor` after
`forge verify generate` and it should report clean.

## What just happened

- The `parser` extra enabled structured RTL port parsing (a regex
  fallback is used without it — see [installation](installation.md)).
- `init-plugin` scaffolded a verification capsule under a real plugin
  directory layout (`forge/verify/...`), matching the layout
  `plugins/passthrough_demo/` and `plugins/trigger_demo/` both use.
- `doctor` gave you an honest, specific status of what's built versus
  what's still missing, rather than a generic pass/fail.

## Next

- [The golden-path tutorial](../tutorials/golden-path.md) — scaffold a
  topology alongside the verification capsule, generate a top level, and
  run a real simulation, using `passthrough_demo` as the running example.
- [Authoring topology contracts](../how-to/author-topology-contracts.md)
  — how to write the `design.yml`/`modules.yml`/interface-contract files
  a real plugin needs.
