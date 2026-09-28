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

Two more extras exist, for other purposes:

- **`docs`** — `mkdocs`, `mkdocs-material`, `mike`. Only needed to build
  this documentation site locally.
- **`all`** — shorthand for `forge[dev,parser,docs]`.

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

### Install a release, not a branch

Pin a release tag:

```bash
pip install "git+https://github.com/pleguina/forge.git@v2.0.0#subdirectory=forge"
```

Every release also publishes a built wheel and source archive with
checksums, release notes, migration notes, the support matrix, and the
environment the release was actually tested in. If your organisation
mirrors artifacts internally, install the wheel directly and verify it
first:

```bash
sha256sum -c SHA256SUMS
pip install forge-2.0.0-py3-none-any.whl
```

**Do not install from a moving branch.** `@main` is whatever was merged
most recently: it will change under you, it is not what any release note
describes, and two machines installing "the same way" a week apart get
different code. Track a tag, and move deliberately when you choose to.
Editable installs from a checkout (`pip install -e "forge[dev,parser]"`)
are for developing FORGE itself — see
[contributing](../development/contributing.md).

## System-level tools

A few things live outside the Python package and are installed separately,
only if you need them:

- **GHDL** — used by `forge topgen lint` for VHDL linting (`apt-get install ghdl` / `dnf install ghdl` / `brew install ghdl`).
- **Verilator** — used by `forge topgen lint-verilog` for Verilog linting only, not as a simulation backend (`apt-get install verilator` / `dnf install verilator` / `brew install verilator`).
- **Vivado / XSim** — required for any `forge verify run` flow that uses the `xsim` backend.
- **Vitis HLS** — required for `forge hls run` and any `hls_csim` verification flow.

None of these are required just to install FORGE and scaffold a plugin —
you'll hit a clear, specific error (or an honest `FAIL` from
`forge verify doctor`) if a step needs one that isn't on `PATH`.

## Next

Continue to the [five-minute quickstart](quickstart.md) to scaffold and
health-check your first plugin.
