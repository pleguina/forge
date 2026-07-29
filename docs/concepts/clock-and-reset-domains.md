# Clock and Reset Domains

FORGE's clock/reset support has two distinct layers that are easy to
conflate. This page separates them so neither gets oversold nor undersold.

## Layer 1: one functional clock/reset per module (this is the model)

Every module's interface contract declares exactly one `clock_primary` and
one `reset_primary` role (see
[contracts and protocols](contracts-and-protocols.md)). The generator and
verifier assume a single functional clock/reset domain **per module** —
that part of the model is not in flux.

The canonical role vocabulary (`forge/topgen/ip/canonical_roles.yaml`)
also declares two further roles, `clock_secondary` and `reset_secondary`,
explicitly marked `status: reserved`. A contract may declare them today —
`ContractVerifier` accepts them and emits a warning, never an error — but
they currently have **no functional effect**: no dedicated wiring, no
validation, no generated behavior beyond appearing in the contract. They
exist to reserve the vocabulary for a future multi-clock-domain-per-module
release; declaring them does not create a second domain inside a module,
and does not trigger any synchronizer insertion. See
`docs/IP_INTERFACE_POLICY.md`'s "Reserved roles" section for the
authoritative wording this page mirrors.

## Layer 2: real, structural cross-module CDC detection (this exists too)

Separately from the per-module role model above, FORGE has a real,
working clock-domain-crossing **checker** for connections *between*
modules: `forge.topgen.ip.cdc.verify_cdc`. It resolves which raw net
drives each module's clock and reset (`forge.topgen.ip.domains.resolve_domain_nets`,
shared with the canonical IR so the two never disagree), and flags any
wired connection between two modules whose resolved clock (or reset) nets
differ and which has no matching `cdc:` declaration on its `design.yml`
connection:

```yaml
connections:
  - from: producer
    to: consumer
    cdc:
      kind: 2ff_sync   # or async_fifo
      depth: 2          # optional
```

This checker only runs under `forge topgen gen-top --strict` — a
non-strict run does not check CDC at all, matching the existing behavior
of the cardinality and topology-group verifiers it was built alongside.
It only ever produces `"error"`-severity issues, no warning tier.

When a connection declares `cdc: {kind: 2ff_sync}`, the structural Verilog
generator (`forge.topgen.generators.structural_verilog`) emits a real
2-flop synchronizer instance (`cdc_sync2ff`) on the crossing signal.
`cdc: {kind: async_fifo}` is accepted as a structural approval — it
suppresses the undeclared-crossing error — but emits no FIFO body; no RTL
is generated for it this release.

`design.yml` also accepts optional, purely descriptive
`clock_domains:`/`reset_domains:` blocks (keyed by the resolved net name,
e.g. `{"derived_from": ..., "ratio": ...}`). These are documentation only
— they do not auto-approve any crossing. Every crossing still needs its
own explicit `cdc:` declaration on the connection.

## What this means in practice

- A **single module** with two independent clocks is not modeled — that's
  what `clock_secondary`/`reset_secondary` would need, and they're inert.
- **Two different modules** running on different clocks, wired together,
  *is* detected structurally, and *can* be bridged with a declared
  `cdc: {kind: 2ff_sync}` adapter that generates real synchronizer RTL —
  but only when you opt into `gen-top --strict`.
- `async_fifo` is a recognized, structurally-valid `cdc.kind` value with
  no generated implementation yet — declaring it silences the crossing
  error, but you still have to provide the actual FIFO instantiation
  yourself.
- There is no timing-closure or CDC-verification-tool integration here —
  this is a structural, name-based check that a crossing was
  acknowledged, not a substitute for STA/CDC signoff tooling.

`forge/tests/test_cdc.py` covers the detection logic,
`test_cdc_generation.py` covers the generated synchronizer RTL text, and
`test_cdc_schema_loading.py` covers `design.yml`-level parsing of `cdc:`/
`clock_domains:`/`reset_domains:` — all real, passing tests, not
placeholders.
