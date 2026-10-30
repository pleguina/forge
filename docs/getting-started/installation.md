# Installation

FORGE is installed from source, into a virtual environment. There is no
PyPI package — see [Distribution](#distribution) below for why, and don't
wait for one.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "forge[dev,parser]"
```

This installs the `forge` CLI in editable mode from the `forge/`
subdirectory of this repository, plus two optional extras:

- **`dev`** — `pytest`, `pytest-cov`, `black`, `mypy`, `types-PyYAML`. Only
  needed if you're contributing to FORGE itself (running its test suite,
  linting, type-checking).
- **`parser`** — `pyverilog`, for structured RTL port parsing. Optional: a
  regex-based fallback is used automatically when it's absent, so plugins
  work without it, just with slightly less precise port extraction.

More extras exist, for other purposes:

- **`plot`** — `matplotlib`, for `forge dev analyze plot-results`, which
  fails without it.
- **`docs`** — `mkdocs`, `mkdocs-material`, `mike`, plus `Pillow` and
  `matplotlib` for the tutorial's figures. Only needed to build this
  documentation site locally.
- **`all`** — shorthand for `forge[dev,parser,plot,docs]`.

If you only want to use FORGE as a plugin author (not contribute to
FORGE itself, not build docs), `pip install -e "forge[parser]"` is enough
— that's what the [quickstart](quickstart.md) uses.

## Distribution

There is no separately-published PyPI package for FORGE, and there won't
be one under the name `forge` — that name is already taken on public PyPI
by an unrelated, actively-maintained project. This is FORGE's permanent
installation story, not a placeholder waiting for a PyPI slot: install
directly from this git repository. See `CONTRIBUTING.md`'s
"Distribution: git install, not PyPI" section for the full rationale.

### Pin a commit, not a branch

No release has been published on GitHub yet: the repository has no
release and no version tag (check
[tags](https://github.com/pleguina/forge/tags) and
[releases](https://github.com/pleguina/forge/releases) for the current
state). Until one is, pin the full commit SHA you tested with:

```bash
pip install "git+https://github.com/pleguina/forge.git@<commit-sha>#subdirectory=forge"
```

Development happens on `forge_devel`; `main` receives periodic syncs of it
and lags behind. The commands and `forge.yml` keys in this documentation
describe `forge_devel`. When a release is tagged, pin that tag instead.

**Do not install from a moving branch.** `@main` or `@forge_devel` is
whatever was merged most recently: it will change under you, and two
machines installing "the same way" a week apart get different code. Move
your pin deliberately. Editable installs from a checkout
(`pip install -e "forge[dev,parser]"`) are for developing FORGE itself —
see [contributing](../development/contributing.md).

## System-level tools

A few things live outside the Python package and are installed separately,
only if you need them:

- **GHDL** — used by `forge dev topgen lint` for VHDL linting (`apt-get install ghdl` / `dnf install ghdl` / `brew install ghdl`).
- **Verilator** — a simulation backend for `forge test` and for `verilator` verification flows, and the linter behind `forge dev topgen lint-verilog` (`apt-get install verilator` / `dnf install verilator` / `brew install verilator`).
- **Vivado / XSim** — the `xsim` simulation backend, for `forge test` and `xsim` verification flows.
- **Vitis HLS** — required for `forge dev hls run` and any `hls_csim` verification flow.

Which simulator runs which kind of verification is listed in the
generated [support matrix](../reference/support-matrix.md).
None of these are required just to install FORGE and scaffold a plugin —
you'll hit a clear, specific error (or an honest `FAIL` from
`forge dev verify doctor`) if a step needs one that isn't on `PATH`.

## Next

Continue to the [five-minute quickstart](quickstart.md) to scaffold and
health-check your first plugin.
