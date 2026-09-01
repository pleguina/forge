# What FORGE Does

A single-page tour of the framework's capabilities, with links into the
detail. If you want the exhaustive command-and-flag listing instead, that
is the [CLI Reference](reference/cli.md), generated from the live parser.

Every capability below is reachable from the CLI. `forge --help` groups the
same commands into a **golden path** (the six you need in order) and
**direct stage access** (the individual stages those six orchestrate).

## The golden path

`forge init my_plugin` runs this entire sequence for you on a new plugin —
scaffold, validate, generate, simulate, report — and is the fastest way to
see the whole framework work. Each stage is also a command in its own
right.

| Command | What it does |
|---|---|
| `forge doctor` | Checks the install: Python and FORGE versions, every simulation/synthesis tool on `PATH`, and the resolved location of each framework resource. Read-only, and honest about which tools are optional. |
| `forge init` | Scaffolds a plugin — topology side and verification side — then chains validate → build → test → report. Skips the simulation stage with a note if no simulator is installed. |
| `forge inspect` | Resolves a design into the canonical IR and prints what it found: modules, instances, connections, clock/reset domains, diagnostics, contract maturity. Never writes unless asked (`--emit-ir`). |
| `forge build` | Generates the structural top level from a design. `--plan` computes without writing; `--apply` performs the generation. |
| `forge test` | Prepares and runs a design's verification flows (`check-only`, `prepare`, `run`). |
| `forge report` | Collects topology, latency, verification results and provenance into one self-contained report bundle with an HTML dashboard. |

## Topology generation

Turning a set of modules and a description of how they connect into a
single structural top level.

- **Three output modes** — structural Verilog, VHDL, or a Vivado Block
  Design TCL script (`forge topgen gen-top --mode verilog|vhdl|bd`). All
  three are driven by the same canonical IR rather than each re-deriving
  the topology.
- **Contract-driven wiring** — modules declare typed roles, and a
  `topology_group` in the design says "wire everything producing family F
  to everything consuming it." Four strategies: auto-match, instance
  assignment, explicit role pairs, and scatter/gather. See
  [Contracts and Protocols](concepts/contracts-and-protocols.md).
- **N-dimensional and array bindings** — `raw_port_prefix`/`count` and
  `raw_port_tpl`/`dims`, with structured `coordinates:` matching.
- **Automatic clock/reset fan-out**, register-stage and delay-cycle
  insertion for pipeline alignment, and named boundary crossings for SLR
  or other implementation-boundary delays.
- **Strict mode** (`--strict`) forbids heuristic name matching and legacy
  patterns, failing the build rather than guessing.
- **Cross-module CDC** — structural crossing detection and synchronizer
  generation. See [Clock and Reset Domains](concepts/clock-and-reset-domains.md).

## Interface contracts

The declarations that make wiring deterministic instead of name-guessed.

- **`forge contract infer`** generates a contract skeleton from a module's
  real ports, emitting every port as a role. For RTL it scans the module's
  own source directly — no intermediate file needed.
- **Declare decisions, not transcription.** `raw_port`, `direction`,
  `width` and `active_level` are optional: FORGE derives them from the
  module's Verilog/VHDL and *verifies* them when you do declare them. What
  needs a human is what no port list implies — `wiring_kind`, `protocol`,
  `partition`/`coordinates`, and array bindings. See
  [Authoring Topology Contracts](how-to/author-topology-contracts.md).
- **`forge core verify-contract`** checks a contract against the real built
  IP, so an omitted or drifted field is caught rather than assumed.

## HLS orchestration

- **`forge hls gen-tcl` / `forge hls run`** generate Vitis HLS scripts and
  run synthesis jobs in parallel, from a module catalog.
- **Port prediction** — an HLS module's RTL ports don't exist until its IP
  is built, and their names shift with the interface pragma and argument
  shape. `forge contract infer --predict` reads the C++ signature,
  pragmas, typedefs and constants and predicts the generated port list, so
  a contract can be drafted and reviewed before the first synthesis run.
  The prediction rules are derived from real synthesis output, and
  anything not reliably predictable — struct padding, AXI bundle widths —
  is reported rather than guessed.

## Verification

- **Contract-driven flows** — a `design.verification.yml` declares
  datasets and flows; FORGE generates the testbench, wave script and flow
  file, then runs them (`forge verify generate|prepare|run`).
- **Backends** — `xsim` and `csim`. Verilator is supported as a lint tool,
  not a simulation backend.
- **XML golden datasets** with per-event selection, plus plugin-authored
  stimulus generation and golden-model hooks. See
  [Verification Model](concepts/verification-model.md) and
  [Datasets and Provenance](concepts/datasets-and-provenance.md).
- **Diagnostics** — `forge verify doctor` reports what a plugin still
  needs; `forge verify release-check` gates release readiness.

## Analysis and reporting

- **Static latency checking** (`forge analyze latency-check`) — builds a
  graph from the topology and finds merge points whose inputs don't
  arrive together, using declared latencies, HLS reports and contract
  data. See [Latency Model](concepts/latency-model.md).
- **HLS report parsing** (`forge analyze hls-report`) into CSV, Markdown
  or HTML.
- **Runtime latency and throughput** comparison against simulation probes.
- **Result plots** from a plugin-defined config, and an aggregated
  **HTML dashboard** (`forge analyze dashboard`).
- **Visual design explorer** — an interactive topology view. See
  [Use the Visual Design Explorer](how-to/use-visual-explorer.md).

## The canonical IR, provenance and reproducibility

- A design resolves **once** into a schema-versioned IR
  (`forge inspect --emit-ir`), and generation is driven by it.
- **Content hashing and diffing** — `forge inspect --diff` compares two IR
  snapshots; `--provenance` writes a manifest recording what a build was
  produced from, and `--explain-staleness` says why an artifact is out of
  date.

## Integration with an external framework

`forge framework import|io-resolve|emit-payload` loads an external
framework's ABI and endpoint manifest and resolves a design's I/O against
it, for dropping a generated top level into a larger platform.

## Machine-readable output

Every `--json`-supporting command emits the same envelope —
`{schema_version, status, diagnostics, artifacts, metrics, next_actions}` —
and one exit-code policy applies CLI-wide: `0` pass, `1` a real finding,
`2` the command could not run. Both are specified in
[CLI Exit Codes](development/cli_exit_codes.md).

## What FORGE does not do

Deliberate scope limits, not gaps awaiting a patch — see
[Project Scope](explanation/project-scope.md) and the support table on the
[home page](index.md):

- Vendors other than AMD/Xilinx (no Intel/Altera, Lattice, or generic ASIC flows)
- cocotb/VUnit or GHDL as a primary simulation path
- Non-XML verification datasets
- Multiple functional clock domains *within* a single module
  (`clock_secondary`/`reset_secondary` are reserved and have no effect;
  cross-module CDC is supported)
