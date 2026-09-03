# What FORGE Does

A single-page tour of the framework's capabilities, with links into the
detail. If you want the exhaustive command-and-flag listing instead, that
is the [CLI Reference](reference/cli.md), generated from the live parser.

Every capability below is reachable from the CLI. `forge --help` groups the
same commands into a **golden path** (the six you need in order) and
**direct stage access** (the individual stages those six orchestrate).

## The golden path

There are two ways in. Starting from nothing, `forge init my_plugin` runs
the entire sequence for you on a new plugin — scaffold, validate, generate,
simulate, report. Starting from a repository you already have,
`forge adopt .` reads it and builds the project model out of its own
sources. Either way, each stage is also a command in its own right.

| Command | What it does |
|---|---|
| `forge doctor` | Checks the install: Python and FORGE versions, every simulation/synthesis tool on `PATH`, and the resolved location of each framework resource. Read-only, and honest about which tools are optional. |
| `forge init` | Scaffolds a plugin — topology side and verification side — then chains validate → build → test → report. Skips the simulation stage with a note if no simulator is installed. |
| `forge adopt` | Reads an existing repository — modules, ports, instantiation graph, clocks, resets, HLS kernels, testbenches, constraints — and writes the project model that follows. Nothing in your tree is moved or rewritten. See [Bring Your Own RTL](getting-started/bring-your-own-rtl.md). |
| `forge check` | Reports whether *this project* is completely and consistently described — the project-level counterpart to `forge doctor`'s installation check. Every blocker comes with the step that clears it. |
| `forge next` | The same assessment narrowed to one recommendation, so the workflow does not have to be memorised. |
| `forge connect` | Records one connection decision — which producer drives which consumer — in `forge.yml`. The non-interactive form of the question `forge adopt` refuses to answer for you. |
| `forge explain` | A deterministic account of one FORGE decision — why two ports are connected, why a port reads as the clock, what an HLS interface is predicted to be, what a diagnostic code means, or why FORGE refused to decide. |
| `forge fix` | Applies the repairs the project's own sources prove, and only those. Previews as a diff by default. |
| `forge migrate` | Brings a whole project up to the schemas this build understands — both project layouts, one preview, one step. Previews as a diff by default. |
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

## Adopting an existing project

Reaching a first generated design without first writing a module registry,
a design topology, or one interface contract per module.

- **`forge adopt`** scans a repository FORGE has never seen and writes what
  follows from it: a concise `forge.yml` you maintain, and a `.forge/`
  directory holding the expanded module registry, design topology and
  inferred contracts FORGE maintains. Your own tree is read, never
  modified.
- **Known, inferred, ambiguous, unsupported.** A fact the source proves is
  written. A convention that resolves unambiguously is written *and*
  recorded with the evidence behind it. Anything with two equally valid
  readings — two consumers a producer's width and direction both fit — is
  written nowhere and comes back as a question with every candidate listed.
- **Limits surface during adoption, not after.** A module with several
  functional clock domains is reported as outside FORGE's envelope, with
  the ways forward, and left out of the generated project rather than
  half-integrated.
- **Opaque modules** are the way forward FORGE can act on. Declaring
  `management: opaque` for a module in `forge.yml` integrates it
  structurally without FORGE claiming to model its internals — the escape
  hatch for vendor IP, encrypted blocks and anything outside the current
  envelope. Its clock and reset pins other than the design's own are routed
  to the generated top level for the enclosing design to drive, rather than
  collapsed onto one net that would silently short distinct domains
  together.
- **Answering the questions.** A decision FORGE will not make can be
  answered three ways, all of which write the same ordinary declaration to
  `forge.yml`: edit the file, run `forge adopt --interactive` and answer the
  questions as they come, or run `forge connect <producer> <consumer>`. A
  project answered interactively is byte-identical to one hand-edited —
  there is no interactive state anywhere, and deleting an answer brings the
  question back.
- **`forge check` and `forge next`** render one project-health model:
  sources, contracts, topology, clock/reset, HLS maturity, latency,
  generated artifacts and verification, with a completion figure and a
  ranked list of what to do. `--json` emits the same
  [standard envelope](development/cli_exit_codes.md) as every other
  command.
- **Ordinary configuration, not a dialect.** Everything adoption writes is
  the same format a hand-authored project uses, so `forge topgen validate`,
  `forge inspect`, `forge build` and `forge report` work on an adopted
  project unchanged.

## Explaining and repairing

- **`forge explain`** gives a deterministic account of any single decision:
  a module, a port, a resolved connection, a clock, an HLS kernel, a
  generated artifact, or a diagnostic code. Connections are explained from
  the canonical IR's own `MatchingEvidence` — wiring method, widths,
  coordinates, protocols, cardinality results and every rejected candidate —
  so the account is what the generators actually consumed, not a
  reconstruction. Where a design does not resolve, the fallback to source
  discovery is stated rather than hidden.
- **A refusal is explained as carefully as a decision.** Asking about an
  ambiguous producer returns every candidate FORGE considered, why none of
  them won, and the command that settles it.
- **`forge fix`** applies only what the sources prove: a contract width the
  RTL contradicts, a contract missing for a module whose ports are right
  there, a `.forge/.gitignore` that has gone. A semantic decision is never
  made here at any level of confidence — not even behind a confirmation
  prompt — and comes back as work for you instead. Every repair previews as
  a diff with the evidence behind it.
- **`forge migrate`** answers the question someone actually has after
  upgrading FORGE: does my project still work, and what does it need? It
  finds every schema-bearing file in either project layout, works out what
  each needs, and previews the whole chain as one diff. It performs no
  migration itself — each is delegated to the same function
  `forge topgen migrate` uses, so the two routes cannot diverge — and a
  migration that *moves files* is reported with the command that performs
  it rather than performed by a preview command. Dry-run by default,
  idempotent, and it warns before writing to a project that is not under
  version control.

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

## HLS interface maturity

An RTL module's ports are a fact. An HLS module's are a *prediction* from
its C++ signature until Vitis HLS has synthesised it — a good one, made by
rules taken from real synthesis, and still a prediction.

- **A maturity ladder**, reported per kernel: `inferred_from_cpp` →
  `predicted` → `synthesized` → `reconciled` → `verified`. `forge check`
  reports where each kernel sits; `forge explain hls:<module>` draws the
  ladder with the module's position marked.
- **Prediction/synthesis reconciliation.** Once an IP is built, FORGE
  compares the predicted interface against the one the IP actually exposes
  and classifies every difference — width, direction, unexpected and
  missing ports — attaching a cause where it recognises one (a byte-rounded
  struct member, a handshake pin the block protocol adds). A difference
  with no known cause is reported unexplained rather than given a
  plausible-sounding reason.
- **No vendor toolchain needed to reason about it.** Maturity is read from
  the filesystem and reconciliation compares two port lists, so all of this
  runs in CI without Vitis HLS. The tool is required to *produce* the
  synthesised side, never to interpret it.

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
- **Visual design explorer** — an interactive topology view that also
  shows the design's *open decisions*: a producer with several equally
  valid consumers, and for each candidate the exact `forge.yml` entry and
  `forge connect` command that settles it. The page decides nothing and
  writes nothing — the candidates are `forge check`'s own, and the answer
  lives in the project file. See
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
