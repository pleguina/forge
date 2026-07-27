# Contributing

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "forge[dev,parser]"
```

`dev` pulls in `pytest`, `pytest-cov`, `black`, `mypy`. `parser` pulls in
`pyverilog` for structured RTL port parsing (optional — a regex fallback is
used if it's absent).

## Running things locally before you push

```bash
# Lint
flake8 forge/core forge/topgen forge/hls forge/verify forge/framework forge/analyze
yamllint -d relaxed plugins/trigger_demo/forge/designs/design.yml plugins/trigger_demo/forge/modules.yml

# Agnosticism guard — forge/ core must not hardcode detector/algorithm assumptions
bash ci/agnosticism_check.sh

# Unit tests
python3 -m pytest forge/tests -q
python3 -m pytest plugins/trigger_demo/forge/verify/tools/tests -q

# Full fresh-user onboarding path (install, CLI smoke, init-plugin scaffold,
# trigger_demo doctor, both test suites)
bash ci/fresh_user_check.sh

# Full end-to-end pipeline (requires Vitis HLS + Vivado xsim on PATH)
./run_trigger_demo.sh
```

These are exactly the checks CI runs — see `.gitlab-ci.yml`,
`ci/framework-base.yml`, and `ci/framework-release.yml`. If it doesn't pass
locally, it won't pass in CI either.

## Adding a plugin

Start with `forge verify init-plugin <plugin_id>`, then read
`docs/MINIMAL_CONSUMER_QUICKSTART.md` and `docs/PLUGIN_AUTHOR_GUIDE.md`.
`plugins/trigger_demo/` is the maintained reference implementation —
`plugins/trigger_demo/CANONICAL_PATTERNS.md` walks through every supported
topology pattern with a pointer to the exact files that implement it.

## Keeping FORGE core agnostic

`forge/` (excluding `forge/tests/` fixtures) must not hardcode
detector-type names, trigger-role names, or accelerator timing constants —
those belong in a plugin's own config, or as an explicit, overridable
default (see `forge/framework/io_resolver.py`'s `detector_input_roles`
parameter or `forge/topgen/config.py`'s `reference_period_ns` field for the
pattern). `ci/agnosticism_check.sh` enforces a best-effort version of this
in CI; read its header comment before adding an allowlist entry to it.

## Commit and PR conventions

- Keep commits scoped to one logical change; the commit message should
  explain *why*, not just *what* (the diff already shows what changed).
- Update `CHANGELOG.md` under `[Unreleased]` for any user-visible change
  (new command, changed default, bug fix, breaking change).
- If a change is breaking for existing consumers (renamed command, moved
  path, changed default behavior), add a note to `MIGRATION.md`.
- Docs and code should land in the same change — this repo has previously
  gone long stretches with docs describing a layout that no longer existed;
  don't reintroduce that.
