# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
This file starts from the `rename/forge` branch — history before that point
is available via `git log` but isn't backfilled here. See `CONTRIBUTING.md`
for the versioning policy.

## [Unreleased]

### Fixed
- **`forge analyze latency-check`**: connections between two multi-instance
  modules wired via a single 1-D `port_map_ranges` entry (`count` matching
  the smaller side's instance count — the common array-role pattern, e.g.
  "52 array-input modules each feeding their own delay-line instance")
  were silently expanded into the full producer × consumer Cartesian
  product instead of the declared 1:1 positional pairing. This broke
  `_upstream_chain_latency`'s single-real-predecessor chain-folding for
  any merge point reachable through such a connection, silently dropping
  upstream latency from the accumulated total. Found on a real,
  production external consumer's design: a 52-instance ranged connection
  was expanding to 2704 edges. Fixed in both `_build_graph_from_ir` and
  the `_build_graph_legacy` fallback (kept in sync, per
  `test_latency_graph_ir_equivalence.py`'s existing equivalence
  guarantee); 2 new regression tests
  (`test_ranged_multi_instance_connection_pairs_positionally_not_cartesian`,
  `test_ranged_multi_instance_chain_folds_through_into_a_real_merge_point`).
  Neither reference plugin exercises this connection shape, so this had
  no test surface before. Narrow, deliberate scope: multiple
  `port_map_ranges` entries on one connection, the N-D `dims` form, and
  `topology_group` connections all keep the existing conservative
  Cartesian-product fallback — see `graph.py`'s module docstring for why
  that default is otherwise correct, not a bug, for the genuinely
  ambiguous cases it still covers.
- **`forge analyze latency-check`**: added `resolve_conn_map()`, an
  optional, best-effort real per-instance connection map (same contract
  resolution `forge.ir.build` uses — `load_contracts_for_design` +
  `synthesize_ip_info` + `auto_match_ports`, no built IP required, just
  contracts) that the CLI now resolves automatically whenever
  `--contracts-from` is given. When it resolves, it supersedes both the
  plain Cartesian-product default and the `port_map_ranges`-only fix
  above for *every* connection and `topology_group` uniformly — including
  partition/`instance_assign`-based wiring, which neither of those can
  resolve without contract data. Found needed on the same real
  production design: several real merge points (a region-filter/priority-
  arbiter fan-in fed by 5 differently-sized RPC delay-line arrays) were
  only resolvable this way. Falls back silently to the existing
  contract-free heuristics when contracts aren't available or don't
  resolve, so nothing changes for a design.yml checked before any
  contract exists.
- **`forge analyze latency-check`**: `_upstream_chain_latency` was
  treating a node's single real predecessor's *explicit*
  `variable_latency`/`elastic` declaration (e.g. an async config-tap
  module) as poisoning the *current* node's own, separately-known, fixed
  latency to "unknown" — even though that node's own declared/HLS-report
  value already fully describes its own behavior regardless of when the
  async signal arrives. Found on the same real design: `dt_interface`/
  `csc_interface` instances each have exactly one graph predecessor
  (their config tap, not their true unmodelled top-level-external data
  input), which was turning an otherwise real, balanced merge point into
  a false "unknown". Fixed to stop folding at an *explicitly*
  variable/elastic predecessor and keep the current node's own value —
  deliberately narrower than "any predecessor without a usable cycle
  count": a predecessor with no declared latency at all (genuinely
  missing data, not an architectural fact) still propagates as unknown,
  unchanged. 2 new regression tests
  (`test_explicitly_variable_predecessor_does_not_poison_a_node_with_its_own_known_latency`,
  `test_undeclared_zero_cycle_predecessor_still_propagates_as_unknown`).
- **`forge analyze latency-check`**: added `Connection.control_strobe`, a
  new declaration marking a connection as a control/reset/output-stamp
  pulse rather than a data path, excluded entirely from merge-point
  detection (not merely marked "unknown" the way an `unknown_cdc` edge
  is — a node fed by one real data predecessor plus a control strobe is
  not a merge point at all). Found needed on the same real design: a
  bunch-crossing timing controller distributes several such strobes
  (reset/bank-swap/output-stamp pulses) whose arrival cycle is
  deliberately derived, by the receiving design's own logic, from each
  stage's accumulated datapath depth — not a second data source
  requiring exact-cycle reconciliation against a sibling data path. This
  was previously producing false mismatches comparing the strobe
  source's own flat latency against real datapath predecessors. 2 new
  regression tests
  (`test_control_strobe_predecessor_is_excluded_from_merge_point_detection`,
  `test_control_strobe_alongside_two_real_predecessors_leaves_a_real_merge_point`).
- **`forge analyze latency-check`**: `_upstream_chain_latency` never
  folded through a real merge point (>=2 real predecessors), even once
  independently confirmed balanced (every real predecessor resolving to
  the same known total) — discarding that node's real accumulated total
  in favor of just its own scalar latency for every downstream
  comparison. Fixed to fold through when — and only when — a merge point
  is confirmed balanced; a genuinely mismatched or unknown one still
  keeps the previous conservative (own-latency-only) behavior, so
  `check_merge_points`'s own loop remains the one place that flags a
  real issue. Found on the same real design: a genuinely balanced
  concentrator merge point (two real predecessors both totalling 11) was
  reporting "0" to every merge point downstream of it, turning a real
  ~2-cycle discrepancy further downstream into a misleading ~13. 2 new
  regression tests
  (`test_chain_folds_through_a_confirmed_balanced_merge_point`,
  `test_chain_stays_conservative_through_a_genuinely_mismatched_merge_point`).

- **Reference-plugin test rot**: `passthrough_demo`'s
  `test_single_flow_declared` still asserted a single declared flow long
  after the plugin grew to a real 4-flow matrix (xsim/verilator x
  svh_include/readmemh). Replaced with `test_declared_flow_matrix`,
  which pins the whole (name, backend, stimulus_mode) matrix instead of
  just its first entry.
- **Vision-pipeline tutorial reference assets**: the committed
  `docs/assets/generated/vision-pipeline/manifest.json` recorded stale
  `design_content_hash` values for all six diagrams, failing both
  `generate_reference_assets.py --check` (the docs-build CI job) and
  `test_check_passes_against_the_currently_committed_assets`.
  Regenerated. Root cause worth knowing: `content_hash()` digests the
  entire canonical IR JSON, so the purely additive declared-latency
  fields added to `forge/contracts/config.py` above shifted every
  design's hash even for designs that never set them. The rendered SVGs
  and PNGs are byte-identical -- only the recorded hashes moved.

- **`forge init` no longer requires a simulator.** The scaffolded flow
  simulates with xsim, so on any machine without Vivado the flagship
  onboarding command died at stage 6 of 7 — scaffold, validate, build and
  report all work fine without one. A missing simulator now skips just
  that stage with a visible note and a `next_actions` entry saying how to
  run it later, and `forge init` still reports PASS. Found by running the
  command with the toolchain off `PATH`.
- **`forge init` output**: 281 lines down to ~36. Every stage is a full
  `forge` command with its own banners and epilogue, and relaying all
  seven verbatim printed the same design-validation report three times
  and recommended two "Next steps" blocks of commands `forge init` had
  already run. Stages now report one line each; `--verbose` restores the
  full transcript, and a failing stage still dumps its own output.
- **Design validation false positives.** Two warnings fired on designs
  that were entirely correct, so no reference plugin — and no fresh
  scaffold — could validate clean. `validate_connections` warned "No
  connections defined between modules" without checking `topology_groups`
  (the contract-driven style the docs recommend) or the module count,
  telling users of a correctly-wired design to adopt the legacy scalar
  style; `validate_consistency` in the same file already handled
  topology_groups correctly. `validate_timing` compared the clock against
  a *fallback* reference period of 25.0 ns — the LHC 40 MHz BX period —
  even when the design declared none, so every detector-agnostic scaffold
  was told its clock didn't divide a period it had never heard of. The
  check now runs only against a declared reference period.
  `generators/design_parameters.py`'s fallback is unchanged: it feeds
  generated testbench timing, and changing it would alter existing
  designs' output.
- **Unknown keys in a design.yml** surfaced as a bare
  `TypeError: __init__() got an unexpected keyword argument 'part_name'`
  — realistically the most common first-hour mistake, with the worst
  error message in the CLI. `DesignConfig.load` now rejects unrecognised
  keys with the file, the key, and the closest real field name
  (`'part_name' — did you mean 'part'?`), via a new
  `UnknownConfigKeyError`.
- **Exit code 2 for user config errors**, against the policy in
  `docs/development/cli_exit_codes.md` — which reserves 2 for a command
  that could not run and says it is "never a real finding". A malformed
  design.yml is a real finding, but `forge topgen validate` returned 2
  where `forge build` returned 1 for the identical file, and the error
  advised `--debug` for a traceback, framing the user's typo as a FORGE
  crash. New `envelope.status_for_exception()` classifies input errors as
  `fail`/1 in one shared place; `print_cli_error` no longer offers a
  traceback for them.
- **`forge report`'s "self-contained" bundle embedded absolute paths.**
  `render_markdown_summary`'s `_section` interpolated the full `Path`
  where the topology block three lines above correctly used `path.name`,
  so `summary.md` broke as soon as the report directory was moved,
  published, or opened elsewhere. Links are now relative to the summary
  itself, with paths that genuinely live outside the bundle keeping their
  absolute form.
- **`forge inspect`** printed a raw Python dict for contract maturity
  (`{'total': 1, 'contract_driven': 1, …}`) and misaligned its first
  label by one character. Maturity now renders as `1/1 contract-driven`
  for humans (the dict is unchanged in `--json`) and every label is
  padded to one column.
- **`ip_info_key` footgun**: the scaffolded interface contract carried a
  comment warning that this field must match the design *instance* name,
  noting that getting it wrong "is a real, previously-hit bug" — the
  knowledge lived only in a comment. The field now defaults to
  `module_name`, and a key that doesn't resolve reports the keys that are
  actually present plus what the field must match.
- **Stale package paths in docs and plugin comments** after the
  shim-free `verify`/`analyze`/`topgen` renames — `forge/topgen/config.py`,
  `forge/verify/design_contract.py` and four others, in
  `cli_exit_codes.md`, `MIGRATION_TOOLING.md` and two vision_pipeline
  design comments. `ci/stale_reference_check.sh` only guarded dotted
  *import* paths, so dead slash *file* paths drifted freely; it now
  checks those too, carefully enough not to flag a plugin's own
  `forge/verify/` capsule directory, which is a real and current path.

- **Verilog port scanner corrupted multi-name declarations.**
  `output wire [W-1:0] a, b, c` declares three identical ports, but the
  ANSI header blob is split on commas before being matched, so only `a`
  arrived carrying a direction and width — `b` and `c` fell through to a
  hardcoded `('in', 1)` default, turning 8-bit outputs into 1-bit inputs.
  Found in `vision_pipeline_demo`'s `window_builder_rtl`, where all six
  affected window taps were invisible because the hand-written interface
  contract restated the correct values on top of them; it would bite
  immediately in compat mode, where no contract exists to correct the
  scan. Bare names in an ANSI port list now inherit from the preceding
  item, as Verilog specifies, without shadowing the non-ANSI path where a
  bare header name resolves from a body declaration. 2 regression tests.

### Added
- **Interface contracts may omit the port facts their own source states.**
  `raw_port`, `direction`, `width` and `active_level` are now optional:
  `contract_loader` derives them from the module's Verilog/VHDL at load
  time, and *verifies* rather than overwrites anything the contract does
  declare, so a declaration stays available as an assertion. Measured
  across this repo's 27 contracts, those four fields are ~55% of all
  content lines and 308 of 324 roles carry nothing else — they restate
  the port list the module already declares, which is also why a contract
  verifier had to exist to catch them drifting. A role that decides
  nothing collapses to its name. Fully backward compatible: a contract
  that declares everything never triggers a scan.

  Measured on the reference plugins, contracts shrink ~2.4x with prose
  notes kept (3.5x counting structure alone), and `passthrough_demo`'s
  generated top level is byte-identical before and after slimming.

  Scope is honest about where port truth exists: 73% of roles are RTL and
  resolve pre-build. An HLS module has no HDL to scan until its IP is
  built — the contract is itself the pre-build stand-in for its ports —
  so those must stay explicit. A contract that omits fields nothing can
  resolve now says so at load, naming the roles and the reason, instead of
  silently dropping the role from contract-driven wiring and surfacing
  much later as "role not found in producer contract".
- **`forge contract infer`** — a real home for contract authoring.
- **`forge.hls.port_prediction`** — predicts the RTL ports Vitis HLS will
  generate for an HLS module, so a contract can be drafted and reviewed
  before the first synthesis run. This is what closes the gap left by the
  contract-derivation work above: an HLS module has no HDL to scan
  pre-build, and its port names shift substantially with the interface
  pragma and argument shape.

  The rules come from **real Vitis HLS 2024.1 output**, not documentation.
  `forge/tests/fixtures/hls_port_matrix/` holds three top functions
  covering the interface/pragma combinations plus the exact port list each
  synthesises to; the tests assert the predictor reproduces it, and the
  fixture is the specification when they disagree.

  Validated against two independent real production modules never used to
  write the rules: `golden_proc_hls` predicts **261 of 261 ports exactly**
  — every name, direction and width, including 252 from a
  `hit_t[18][14]` argument scalarised by `ARRAY_PARTITION complete dim=0`.
  `inputProcessor`, the same array shape under a bare `complete` (which
  defaults to `dim=1`, leaving a BRAM interface), predicts with **zero
  spurious ports**.

  What it declines to predict is as deliberate as what it does. Struct
  widths are reported unknown, because HLS pads members rather than
  concatenating them — a 16-bit-logical struct in the fixture synthesises
  to 48 bits, so a summed width is wrong in a way that looks right.
  `s_axilite`/`m_axi` bundles depend on widths chosen at synthesis. And a
  memory interface is predicted single-port with a warning, since HLS adds
  a second port set when the schedule needs two accesses per cycle — a
  scheduler decision, and on `inputProcessor` exactly the set the
  prediction missed.

  Coverage is the same for data ports and for the single-bit signal ports
  the interface mode generates — the strobes, enables and handshakes that
  appear in no form in the C++ (`_ap_vld`, `_ap_ack`, `_ce0`, `_we0`,
  `_empty_n`, `_read`, `TVALID`/`TREADY`, and the block-protocol
  `ap_start`/`ap_done`/`ap_idle`/`ap_ready`). Stratified by width across
  all four validation modules: 1-bit and wide ports both predict exactly,
  with zero spurious ports anywhere, and the only misses are the
  second-memory-port set already described. One rule came out of that
  check — with no INTERFACE pragma a by-value input defaults to `ap_none`
  but a by-reference output defaults to `ap_vld`, and so carries a
  `_ap_vld` strobe. Two further findings the matrix settled: AXI-Stream
  `TDATA` rounds up to a byte multiple, and adding any AXI interface flips
  the block reset from `ap_rst` to `ap_rst_n`.

### Changed
- **`forge contract infer` replaces `forge topgen migrate --kind
  infer-contract`.** Writing an interface contract is the most common
  authoring task in a FORGE project; it was filed under a verb meaning
  "convert an old project". The command also required the caller to
  produce an `ip_info.yaml` first, and returned every data port as a TODO
  *comment* to be retyped as YAML — useless for exactly the bulk of the
  work. It now emits every observed port as a real role (by name alone
  for RTL, with direction/width for HLS, since those can't be derived
  pre-build), and for RTL it scans the module's source straight from
  `modules.yml` with no intermediate file. `window_builder_rtl`'s
  generated contract is 75 lines against 167 hand-written, and resolves
  with no conflicts. Removed from `migrate` outright rather than
  shimmed, per ADR 0005's pre-release policy.
- **`normalization_status: draft` is now enforced.** The field was
  declared on every contract and read by nothing, so a generated skeleton
  could be committed and built against as though a human had checked its
  wiring semantics. `forge topgen validate` reports a draft contract, and
  `--strict` fails on it.

- **`forge --help` is tiered.** Twelve top-level entries were listed flat,
  with the golden-path verbs (`init`/`inspect`/`build`/`test`/`report`)
  as undifferentiated peers of the stage groups they wrap, so nothing
  told a newcomer where to start. The epilog and the subcommand
  registration order now lead with the golden path and file the stage
  groups under "Direct stage access". `forge build --apply` also stopped
  printing "delegating to `topgen gen-top`", which taught the older verb
  at the moment the newer one was working.
- **The Quickstart now starts the tool up instead of failing it.** It
  scaffolded with `forge verify init-plugin` (half a plugin: the
  verification side only), ended on a `doctor` `FAIL`, and spent a
  paragraph explaining why that failure was expected — never running a
  simulation or producing an artifact, and never mentioning `forge init`.
  It now runs `doctor`, `init` and `inspect`, and ends on a dashboard.
  `ci/quickstart_commands.sh` (its single source of truth, sourced by
  `ci/fresh_user_check.sh`) was rewritten to match.
- **`forge topgen init-plugin`'s "Next steps"** recommended
  `forge topgen gen-top` and told the reader to scaffold the verify half
  next; it now names the golden-path equivalents and points at
  `forge init`. Scaffolded files no longer credit
  `forge topgen init-plugin` for work the user did with `forge init`.

  "private CMS/OMTF-internal tooling, not a general-purpose public
  release" as the explicit reason git-install (not PyPI) is the
  permanent distribution story. That framing is now retired — FORGE
  originated as CMS/OMTF trigger/DAQ firmware tooling at CERN but is
  released as a general-purpose, detector-agnostic open-source project
  (MIT-licensed; copyright remains the individual author's, per
  `LICENSE`). The git-install-not-PyPI decision itself is unchanged
  (still just the `forge` name collision on public PyPI), just no
  longer framed as evidence of staying private. Added `CODE_OF_CONDUCT.md`
  (Contributor Covenant v2.1) and `.github/` issue/PR templates.
  Updated `SECURITY.md`'s reporting channel from "confidential GitLab
  issue" to GitHub private security advisories. `README.md`,
  `CONTRIBUTING.md`, and `forge/pyproject.toml`'s `[project.urls]` now
  point at a placeholder `github.com/<org>/forge` pending an actual
  migration decision — the CERN GitLab origin remains authoritative
  until that lands; `ci/plugin-consumer.yml`'s real install source was
  deliberately left untouched since it's live CI configuration, not
  policy documentation.
- **Public host decided**: `github.com/pleguina/forge` is now the
  canonical repository. `README.md`, `CONTRIBUTING.md`,
  `forge/pyproject.toml`'s `[project.urls]`, `mkdocs.yml`, and the
  getting-started docs' install instructions now point at it instead of
  the `<org>` placeholder. The CERN GitLab origin
  (`gitlab.cern.ch/p2u-omtf-ops/arc-framework`) is retained for its
  commit history but is no longer the primary source; the GitHub history
  starts fresh from a single squashed commit rather than carrying that
  history over. `ci/plugin-consumer.yml`'s install source was updated to
  match (it's the one file the prior entry above deliberately left
  untouched, since it's live CI configuration rather than policy
  documentation — now updated because it's a functional install URL that
  needs to stay correct). GitHub Actions CI is a separate, not-yet-done
  follow-up; `.gitlab-ci.yml` remains the only CI pipeline for now.

### Added
- `forge topgen init-plugin <plugin_id>`: scaffolds the topgen side of a
  new plugin (`forge/modules.yml`, `forge/designs/design.yml`,
  `forge/interfaces/<plugin_id>.interface.yaml`, and an
  `algo/rtl/<plugin_id>.v` RTL stub) — previously only `forge verify
  init-plugin` existed, scaffolding the verify/ half; there was nothing
  for topology generation, so a new user hand-authored 4+ files from
  scratch. `plugin_id` is used as both the module `ref` and the
  design.yml instance name, so `ip_info_key` trivially matches the
  design instance name from the start — sidesteps the exact mismatch bug
  found and fixed in `passthrough_demo`'s own contract (see below).
  `forge topgen gen-top` succeeds against the freshly scaffolded files
  immediately, with zero hand-editing — proven end to end by
  `tests/test_topgen_init_plugin.py`. Supports `--dry-run`.
  `docs/MINIMAL_CONSUMER_QUICKSTART.md`'s checklist step 1 now points here.
- `--dry-run` for `forge topgen gen-top`. It previously only existed on
  `topgen clean` (which deletes files) — backwards from what most users
  want, since `gen-top` is the command that writes/overwrites 6-8
  generated files. Runs validation, IP-info resolution, and port
  matching (the real, useful signal — warnings, strict-mode failures,
  wiring method counts) and prints the full list of paths that would be
  written, without calling any generator. One documented exception:
  `ip_info.yaml`, if missing, is still generated even in `--dry-run`,
  since it's a required input to compute the port-matching preview, not
  a `--dry-run`-skippable output — called out explicitly in the command's
  own output when it happens.
- `--json` for `forge topgen validate` and `forge topgen validate-registry`.
  Previously only `verify doctor`/`verify release-check` supported
  machine-readable output; every other command was human-text-only, so
  nothing outside the framework's own GitLab CI could consume validation
  results programmatically. Serializes the existing `ValidationError`
  dataclasses (`forge/topgen/validation.py`) directly — no new report
  type needed.
- `forge doctor`: a new top-level command (sibling to `core`/`topgen`/
  `hls`/`verify`/`analyze`/`framework`, not nested under any group) that
  checks whether the local `forge` install/environment is sane —
  `forge` version, Python version, `xvlog`/`xelab`/`xsim`/`ghdl`/
  `verilator` on `PATH`, `pyverilog`/`matplotlib`/`numpy`/`networkx`
  importability, and the same framework resource paths as `forge core
  resources`. Previously `forge verify doctor` was the only "doctor"-
  shaped command, but it checks one plugin's flow artifacts against a
  specific design — nothing answered "is my `forge` install sane" before
  a user hit a failure deep inside whatever command needed a missing
  tool. Every check is an optional extra today, so exit code is always 0
  unless `--strict` is passed. Supports `--json`.
- `ci/stale_reference_check.sh` (`forge:stale-reference-check` in CI): a
  standing guard against the exact class of bug this branch kept
  rediscovering by hand — a CLI hint, docstring, or generated-artifact
  default quietly referencing `fw_verify`, bare `topgen`, or
  `framework/verify/python` after they stopped existing.
- Real test coverage for three previously-0%-covered, actually-used
  modules: `forge/core/stale_detection.py`, `forge/verify/stimulus_contract.py`,
  `forge/verify/manifest_compile.py`. Raised the coverage floor from 22%
  to 26% to match (measured: 27.09%).
- `tests/test_cli_help_consistency.py`: a regression guard that walks the
- `ci/stale_reference_check.sh` (`forge:stale-reference-check` in CI): a
  standing guard against the exact class of bug this branch kept
  rediscovering by hand — a CLI hint, docstring, or generated-artifact
  default quietly referencing `fw_verify`, bare `topgen`, or
  `framework/verify/python` after they stopped existing.
- Real test coverage for three previously-0%-covered, actually-used
  modules: `forge/core/stale_detection.py`, `forge/verify/stimulus_contract.py`,
  `forge/verify/manifest_compile.py`. Raised the coverage floor from 22%
  to 26% to match (measured: 27.09%).
- `tests/test_cli_help_consistency.py`: a regression guard that walks the
  *live* argparse tree (`forge.core.cli.main.build_parser()`) and
  cross-checks it against the hand-written `--help` epilog/hint text —
  every documented group, subcommand, and flag in the top-level epilog is
  verified to actually exist, and every registered command's `--help`
  is exercised end-to-end. `stale_reference_check.sh` only greps for
  known-dead patterns; this catches the class of drift it can't (a new
  group or renamed flag silently missing from the hints). Immediately
  caught the `framework` group missing from the top-level epilog and
  `--group` help text, and one epilog example using a flag
  (`forge verify run ... --plugin`) — the drift class this test exists
  to prevent — see Fixed.
- `tests/test_topgen_cli_commands.py` and
  `tests/verify/test_verify_cli_commands.py`: real, in-process coverage
  for `core/cli/groups/topgen.py` (13% → 56%) and `forge/verify/__main__.py`
  (15% → 39%), driving the actual CLI entry points against
  `plugins/passthrough_demo` instead of only being exercised indirectly
  through integration scripts. Raised the coverage floor from 26% to 34%
  to match (measured: 34.73% in a from-scratch sandbox venv — the
  pre-existing 27.09% claim above didn't reproduce there either,
  measuring 13.4% before this change; some fixture or environment
  difference from whatever produced that number, worth tracking down
  separately). In-process rather than subprocess: a
  subprocess child interpreter's line execution is invisible to
  `--cov=forge` running in the parent pytest process, so the existing
  subprocess-based CLI tests (e.g. `test_topgen_cli_error_handling.py`)
  never actually counted towards coverage despite exercising real code.
- `tests/verify/test_verify_run_xsim.py` (marked `integration`, skipped
  automatically when xvlog/xelab/xsim aren't on `PATH`): the follow-up to
  the above that actually exercises `_cmd_run`/`_cmd_generate`/
  `_cmd_prepare` — the simulation-execution bodies the CLI-level tests
  deliberately left untested. Copies `plugins/passthrough_demo` into an
  isolated `tmp_path` "consumer root" (nothing tracked in git is ever
  touched), runs the real, documented pipeline
  (`topgen gen-top` → `verify generate` → `verify run`) against it, and
  drives an actual `xvlog` → `xelab` → `xsim` simulation to completion —
  plus one negative case (delete the gen-top'd RTL, confirm `run` fails
  at preflight instead of reporting a false pass). Pushed
  `forge/verify/__main__.py` 39% → 66%, and pulled in real first-time
  coverage on the backend modules it drives:
  `verify/backend_xsim.py` (0% → 60%), `verify/gen_sim.py` (0% → 71%),
  `verify/preflight.py` (0% → 80%), `verify/subprocess_wrapper.py`
  (0% → 75%). Raised the coverage floor from 34% to 40% to match
  (measured: 40.77%).
- `tests/test_core_utils_common.py`, `tests/test_core_constants_and_exceptions.py`,
  `tests/verify/test_port_parser.py`: first-time coverage for four small,
  previously-0%-covered pure-function modules —
  `forge/core/utils/common.py`, `forge/core/constants.py`,
  `forge/core/exceptions.py`, `forge/verify/port_parser.py` (a documented
  backward-compat shim over `rtl_introspection`, not dead code — kept for
  external plugin callers that haven't migrated their imports). All four
  now 100%. Includes a direct regression test for the
  `forge.core.core.exceptions` import-path bug fixed above:
  `load_yaml_safe()` on a missing/invalid file now raises the intended
  typed exception instead of `ModuleNotFoundError`. Raised the coverage
  floor from 40% to 41% to match (measured: ~42%).
- `tests/test_core_cli_group.py`, `tests/test_framework_cli_group.py`,
  `tests/test_hls_cli_group.py`, `tests/test_analyze_cli_group.py`: same
  in-process CLI-driving recipe applied to the remaining four `forge`
  command groups (`core`/`framework`/`hls`/`analyze`), which had only
  ever gotten this treatment for `topgen`/`verify` so far:
  `core/cli/groups/core.py` 24% → 80%, `framework.py` 22% → 77%,
  `analyze.py` 22% → 71%, `hls.py` 12% → 40% (lower because `hls run`'s
  actual parallel Vitis HLS job-orchestration body is real synthesis —
  out of scope here, only its argument-validation/error paths are
  covered; `hls gen-tcl` is pure Jinja templating and is driven for real
  against `plugins/trigger_demo`'s HLS module registry).
  `analyze plot-results` is skipped (needs `matplotlib`, not installed).
  Raised the coverage floor from 41% to 47% to match (measured: 48.01%).
### Removed
- `forge/topgen/validators.py`: dead, superseded duplicate of
  `forge/topgen/validation.py` (`DesignValidator`), which is the version
  actually wired into the CLI. Never imported anywhere.
- `forge/hls/parse_hls_logs.py`: dead standalone script duplicating log
  parsing already done by `forge/core/cli/groups/hls.py`. Not a registered
  entry point, not imported anywhere.

### Changed
- Closed the open PyPI-vs-git-install question from the `2.0.0` release
  notes: confirmed `forge` is already taken on public PyPI by an unrelated
  package, so git install (already the default in `ci/plugin-consumer.yml`)
  is the permanent distribution story, not a placeholder. Documented in
  `CONTRIBUTING.md`.
- Reduced the `mypy` baseline from 115 to 100 errors: safe mechanical
  fixes (implicit-`Optional` defaults, a stray `any`/`Any` typo, missing
  container annotations) plus two genuinely real latent bugs found along
  the way — see Fixed.
- Further reduced the baseline: deleting the two dead modules (see
  Removed) and the `structural_verilog.py` variable-collision fix (see
  Fixed) together account for ~16 fewer errors under
  `mypy forge/core forge/topgen forge/hls forge/verify forge/framework
  forge/analyze`. (Note: the exact error count is sensitive to the mypy
  version — this repo's pinned mypy no longer supports the
  `python_version = "3.8"` configured in `pyproject.toml`'s `[tool.mypy]`
  and silently ignores it; pinning a version that still supports it, or
  bumping the config to 3.9+, would be worth doing so the count is
  reproducible across environments.)

### Fixed
- `docs/MINIMAL_CONSUMER_QUICKSTART.md` — the doc's own header calls it
  "the canonical onboarding entry point" — had every single
  `plugins/<plugin>/...` path (checklist, authored/generated artifact
  lists, and every command example) missing the `forge/` capsule
  directory introduced by the `arc/` → `forge/` rename in `2.0.0`
  (see `MIGRATION.md`). `docs/PLUGIN_AUTHOR_GUIDE.md` and both real
  example plugins (`passthrough_demo`, `trigger_demo`) already use the
  `forge/` capsule layout; this file was never updated after the rename
  commit (`af18bff`) that introduced it, so a new user following it
  verbatim would create files in the wrong place and every documented
  command would fail to find them. Found by cold-dry-running the
  quickstart end to end against `plugins/passthrough_demo`. Build-
  artifact paths (`ip_info.yaml`, `build_hls_<plugin>/`, `out/...`),
  which are correctly consumer-root-relative rather than
  capsule-relative, were left alone.
- `plugins/passthrough_demo/forge/interfaces/passthrough.interface.yaml`
  was missing `ip_info_key`, a required field per the contract schema
  documented in `PLUGIN_AUTHOR_GUIDE.md` and enforced by
  `ContractVerifier`. Running the quickstart's own documented
  `forge core verify-contract` command against this plugin's real,
  checked-in contract failed with "Missing 'ip_info_key' in contract" —
  found in the same dry run. Added `ip_info_key: pt`, matching the
  `ip_info.yaml` key the RTL compat-mode scan actually produces for this
  design (the design.yml instance name, not the module's `ref:` name).
- `forge core resources` was a documented public command
  (`docs/SUPPORT_CLASSIFICATION.md`, `docs/FRAMEWORK_TOOLING_INTERFACE.md`
  both list it under "Public framework API") whose implementation was a
  permanent stub — `resources: dict[str, str] = {}`, hardcoded empty
  since the file was created — so it always printed nothing regardless
  of `--key`/`--format json`. Now resolves four real framework resource
  paths (`hls_templates`, `canonical_roles`,
  `normalized_signal_families`, `docs_root`) and reports whether each
  actually exists. The resolution logic
  (`resolve_framework_resources()` in `core/cli/groups/core.py`) is
  shared with the new `forge doctor` command below.
- Raised the coverage floor from 41% to 49% to match the CLI/UX work
  above (measured: 49.39%) — all five items are covered by new
  in-process CLI tests following this session's established pattern.

### Fixed
- `forge/verify/rtl_introspection.py`'s `write_port_signature()` was
  annotated `-> None` while its docstring documented (and its
  implementation actually did) return the computed hash string, masked
  with a `# type: ignore[return-value]` rather than fixed. Its only
  caller, `extract_and_write()`, was already relying on the real return
  value despite the type saying it couldn't exist. Corrected the
  annotation and removed the now-unneeded ignore.
- `forge/core/cli/__init__.py`'s unused `main()` wrapper (the real entry
  point is `forge.core.cli.main:main`, used by neither this wrapper nor
  imported anywhere) claimed `-> int` while delegating to a function that
  always returns `None`. Corrected to `-> None`.
- Several dead-command reference bugs the same class as this branch's
  earlier `fw_verify`/`topgen` fixes, found by the new
  `stale_reference_check.sh`: stale `topgen ...` hints (missing the
  `forge` prefix) in `forge/core/stale_detection.py`,
  `forge/core/utils/port_signature.py` (including the default
  `"generated_by"` provenance string written into every generated
  `port_signature.json`), `forge/topgen/generators/sv_testbench_generator.py`,
  and `forge/verify/preflight.py`.
- `forge --help`'s epilog "Groups:" list and the `--group` help string
  both omitted the `framework` group, even though it's fully registered
  and functional (`forge framework import/io-resolve/emit`); one epilog
  example (`forge verify run ... --plugin`) used a flag that only exists
  once `forge verify run` delegates to `forge.verify.__main__`'s own
  parser and wasn't checkable against the top-level tree. Both are now
  covered by `test_cli_help_consistency.py`.
- `forge/core/utils/common.py`'s `load_yaml_safe()` raised
  `from ..core.exceptions import ...` from inside `forge.core.utils.common`,
  which resolves to the non-existent `forge.core.core.exceptions` — any
  YAML parse or file-read failure crashed with `ModuleNotFoundError`
  instead of the intended `ConfigurationError`/`FileNotFoundError`. Fixed
  the relative import.
- `forge topgen match-ports`'s "Global nets" summary crashed
  (`sequence item 0: expected str instance, tuple found`) on any design
  with clock/reset fan-out, because `global_nets` maps each net to a list
  of `(instance, port)` tuples, not port-name strings, and the CLI tried
  to `', '.join()` them directly. This is the exact "Module vs. str"
  class of confusion flagged in `structural_verilog.py` below, just
  undetected because nothing exercised this branch — reproduced and
  fixed against `plugins/passthrough_demo`, a design with only two
  auto-mapped global nets (`ap_clk`, `ap_rst`).
- Investigated `structural_verilog.py`'s 13 "Module vs. str" mypy errors:
  not a live bug. `src_mod`/`dst_mod` hold `Module` objects in one early,
  self-contained loop (register-stage/delay-cycle bookkeeping) and are
  reused as plain instance-name strings in later, unrelated loops (wire
  and instance emission) — legal at runtime since each later read follows
  a fresh string assignment in the same iteration, but mypy unifies a
  name's type across the whole function since Python has no block
  scoping. Renamed the first loop's variables (`src_mod_obj`/
  `dst_mod_obj`) to remove the collision instead of leaving it as a false
  positive.

## [2.0.0] - 2026-07-27

### Added
- `MIGRATION.md`, `CONTRIBUTING.md`, `SECURITY.md`, root `LICENSE`,
  `CODEOWNERS`, this changelog.
- `forge/tests/test_hdl_parser.py` — real coverage for the HDL parameter
  evaluator (previously 0%).
- A `--cov-fail-under` coverage floor (22%, matching the measured baseline)
  so overall test coverage can't silently regress.
- `mypy` now actually runs in CI (`forge:type-check`), report-only —
  the codebase has a pre-existing baseline of ~115 type errors (mostly in
  `forge/topgen/generators/structural_verilog.py` and
  `forge/verify/__main__.py`) that hasn't been paid down yet. The job makes
  that baseline visible without blocking merges on unscoped work.
- `plugins/passthrough_demo/` — a second, minimal, deliberately generic
  reference plugin (one RTL module, no HLS, no CMS-flavored naming
  anywhere) alongside `plugins/trigger_demo/`. Verified end to end
  including a real Vivado xsim run, not just static checks.

### Changed
- **Breaking:** renamed the framework from ARC to FORGE — CLI command
  (`arc` → `forge`), Python package (`arc.*` → `forge.*`), top-level and
  per-plugin capsule directories (`arc/` → `forge/`), CMake project name,
  and all CI anchors/env vars. No compatibility shim; see `MIGRATION.md`.
- `forge.framework` (detector I/O resolution, ABI import, payload
  generation) and `forge.topgen` are now algorithm-agnostic: detector role
  matching, ABI provider validation, algo-port naming, and accelerator
  timing constants are config-driven with documented defaults instead of
  hardcoded CMS/OMTF assumptions. Verified byte-identical generated output
  for the existing trigger_demo/OMTF configuration.
- Rewrote the CI/CD pipeline: every job was silently broken (pre-dating
  even the rename) due to stale paths and commands from before the
  `framework/`→`arc/` flatten and CLI unification. Replaced the dead
  cmake install-tree release-gate proof with a job that runs
  `run_trigger_demo.sh` directly; rewrote `ci/plugin-consumer.yml`
  (the template downstream plugin repos include) to install `forge`
  correctly; consolidated two overlapping fresh-user-check scripts into
  one; added `ci/agnosticism_check.sh` as a standing regression guard.
- Extended `forge:lint` CI coverage to `forge/framework` and
  `forge/analyze`, which were previously unchecked.

### Fixed
- `forge verify` internals (`bootstrap.py`, `conftest.py`, backend
  registry, several test files) imported a top-level `fw_verify` package
  and referenced a `framework/verify/python` directory that hasn't existed
  since the pre-`arc` layout — only "worked" locally due to a stale,
  non-editable `fw_verify` package left in site-packages from an old
  build. Fails cleanly now instead of silently depending on environment
  pollution.
- `forge verify init-plugin` scaffolded new plugins into the wrong
  (pre-flatten) `<plugin>/verify/` layout and baked the same broken
  `fw_verify` sys.path hack into every generated plugin's `bootstrap.py`
  and `gen_stimulus.py`.
- `gen_stimulus.py` (trigger_demo) computed its repo-root path one
  directory level short after the plugin capsule gained its `forge/`
  layer, silently resolving to a doubled `plugins/plugins/...` path.
- `plugins/trigger_demo/forge/verify/tools/trigger_demo_verify_env.sh` had
  the same one-level-short path bug, resolving `TRIGGER_DEMO_CONSUMER_ROOT`
  to `plugins/` instead of the repo root. The four per-flow `run.sh`
  convenience scripts it's sourced from also invoked the dead
  `python3 -m fw_verify run` and had their own `source ../../tools/...`
  path miscalculation (should have been `../tools/...`) and were missing
  `--plugin`/`--consumer-root`. All four committed `verify.flow.yml` xsim
  flow files also had the pre-`forge/`-capsule dataset XML path baked in.
  Found and fixed by actually running the scripts, not just reading them —
  confirmed working end to end afterward with a real `run_trigger_demo.sh`
  execution (HLS csim + synth for all 4 modules, all 9 verification flows,
  including real Vivado xsim simulation) — all 9 flows passed.
- `forge/verify/__main__.py`'s `_doctor_emit()` type-annotated its
  `report` parameter as `"DiagnosticReport"` without the name being
  resolvable anywhere in the module (mypy: `name-defined`) — added a
  `TYPE_CHECKING`-guarded import.
- Root `CMakeLists.txt` unconditionally `add_subdirectory(verify)`'d a
  directory with no `CMakeLists.txt` of its own — `cmake -S . -B build`
  failed outright. Now guarded on the subdirectory actually existing.
- `forge/pyproject.toml` was missing the `parser` extra
  (`pip install -e forge[parser]`) that several error messages already
  told users to install.
- Replaced `eval()` in `forge/core/utils/hdl_parser.py`'s HDL
  parameter-expression evaluation with an AST-based safe arithmetic
  evaluator.
- Fixed a real `F811` duplicate import and an `E741` ambiguous variable
  name, both surfaced once lint coverage was extended to the packages
  that contained them.
