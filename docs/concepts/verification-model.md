# Verification Model

This page describes the shape of FORGE's verification architecture — what
a flow *is*, how backends plug in, and how support is classified. For the
operational steps (how to actually run a flow), see
[Setting up the verification framework](../how-to/setup-verification-framework.md)
and [Integrating verification](../how-to/integrate-verification.md).

## A "flow" is one declared simulation unit

A **flow** is one entry under `flows:` in a plugin's
`design.verification.yml` (the schema owned by
`forge.verify.design_contract`) — it names a `kind`, a `backend`, a DUT
(RTL source, top module, testbench module), a dataset, and simulation
timing defaults. `forge verify generate` turns a flow declaration into a
concrete, runnable artifact set (`verify.flow.yml`, a generated
testbench, `wave.tcl`); `forge verify run` executes it.

Three flow kinds exist today, corresponding to increasing levels of
integration: `hls_csim` (an HLS module's C++ model, no RTL involved),
`single_module_rtl` (one synthesized RTL module in isolation), and
`full_chip_rtl` (the generated `algo_top`, everything wired together).
`plugins/trigger_demo/` exercises all three in its
[three-tier verification pyramid](../tutorials/mixed-hls-rtl-example.md);
`plugins/passthrough_demo/` only needs `full_chip_rtl`, since its one
module *is* the whole design.

## The backend abstraction

A flow's `backend` (`csim`, `xsim`, or `verilator`) is dispatched through
one shared interface: `forge.verify.backend_base.BackendAdapter`. Every
backend adapter — `forge.verify.backend_csim.CsimBackend`,
`forge.verify.backend_xsim.XsimBackend`, and a Verilator adapter — must
implement the same four-step contract, called in this order:

1. `validate_backend_requirements(cfg)` — check prerequisites (toolchain
   presence, environment) without launching anything.
2. `prepare_backend_inputs(cfg, ctx)` — stage compile scripts, `.dat`
   files, Makefiles; idempotent.
3. `run_backend(cfg, ctx)` — actually execute the simulation, returning a
   normalized `ExecutionResult` (success flag, waveform/log/observed-output
   paths, exit code, per-stage duration).
4. `describe_backend_outputs(cfg, ctx)` — declare what output paths this
   backend may produce (not an existence check — a declaration consumed
   by generic reporting).

Each adapter also declares static `BackendCapabilities` (does it produce a
waveform? a probe CSV? does it need a vendor toolchain environment?) and
`required_artifacts`, so framework code dispatches on these flags instead
of hardcoding backend-specific conditionals anywhere else. Framework-owned
backends (`xsim`, `csim`, `verilator`) auto-register on import
(`forge/verify/__init__.py`); a plugin can override or add its own via
`register_backend()` in its `bootstrap.py`.

## `FlowResult`: what a run actually produced

`forge.verify.results.FlowResult` is the structured, schema-tagged result
of one `forge test run`/`forge verify run` invocation: the flow name, the
backend that ran it, and a list of per-event `EventResult`s (each with its
own success flag, diagnostics, checks, artifacts, and duration).
`FlowResult.success` is `True` only when every event succeeded;
`FlowResult.artifacts` is the de-duplicated union of every event's
artifacts. This is the artifact `forge.docsgen`'s reference generator
walks recursively to produce
[the artifact-model reference](../reference/artifacts.md) — this page
deliberately doesn't re-list its fields.

## Supported / experimental / unsupported

Not every `(kind, backend)` combination is production-ready, and FORGE is
explicit about which is which rather than silently accepting anything
that parses. `forge.verify.supported_matrix` is the single authoritative
source: every command that processes a flow declaration
(`generate`/`prepare`/`run`/`doctor`) calls `validate_flow_matrix()`
before doing further work.

Three classifications exist (`FlowClassification`):

- **Supported** — on the handoff-ready path; safe to build on.
- **Experimental** — real and recognized, but not yet proven for general
  use (each carries a stated reason — e.g. TB generation not yet proven
  for that scope, or a legacy alias kept for compatibility).
- **Unsupported** — not recognized at all.

The full, current, enumerated table — every kind, its default and allowed
backends, its classification, and the reason — is generated directly from
this module's real `MATRIX` and lives at
[the support matrix reference](../reference/support-matrix.md); it isn't
duplicated here so it can never drift out of sync with the code that
enforces it.
