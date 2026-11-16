# Clock and Reset Domains

FORGE's clock/reset support has two distinct layers that are easy to
conflate. This page separates them so neither gets oversold nor undersold.

## Layer 1: one functional clock/reset per module (this is the model)

Every module's interface contract declares exactly one `clock_primary` and
one `reset_primary` role (see
[contracts and protocols](contracts-and-protocols.md)). The generator and
verifier assume a single functional clock/reset domain **per module** —
that part of the model is not in flux.

The canonical role vocabulary (`forge/contracts/canonical_roles.yaml`)
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

Also unchanged this release: every generated `algo_top` still exposes
exactly one physical top-level `ap_clk`/`ap_rst` pair
(`forge.generation.generators.structural_verilog`; `--mode vhdl` wraps that same top). A
module's own resolved clock/reset net (see Layer 2) may have a different
*name*, but it is always aliased back onto that single literal top-level
port — no design in this repo has ever declared a real second physical
top-level clock, and generating one is out of scope here. See "What's
deferred" below.

## Layer 2: real, structural cross-module CDC detection and synchronization

Separately from the per-module role model above, FORGE has a real,
working clock-domain-crossing **checker** for connections *between*
modules: `forge.contracts.cdc.verify_cdc`. It resolves which top-level net
drives each module's clock and reset (`forge.contracts.domains.resolve_domain_nets`,
shared with the canonical IR and the generators so they never disagree), and flags any
wired connection between two modules whose resolved clock (or reset) nets
differ and which has no matching `cdc:` declaration on its `design.yml`
connection.

Under `connect_clock`/`connect_reset`, every standard clock or reset
name (`clk`, `ap_clk`, `*_clk`, `rst`, `ap_rst`, ...) is wired to the one
global `ap_clk`/`ap_rst` net, so those modules share a domain whatever their
port is called. Only a clock with its own top-level net (`clk_b`) is a
separate domain.

An undeclared crossing is an error (`ATG023` clock-domain / `ATG024`
reset-domain crossing) with or without `--strict`: `forge build` and
`forge dev topgen gen-top` refuse to generate, and `forge dev topgen validate`
fails whenever the design's registry and interface contracts can be loaded
without needing already-built HDL.

### The CDC kinds

```yaml
connections:
  - from: producer
    to: consumer
    cdc:
      kind: level_sync          # or pulse_sync, mailbox_transfer, async_fifo, waived
      depth: 4                  # required for async_fifo; must be a power of two
      min_spacing_cycles: 8     # required for pulse_sync
      reason: "..."             # required for waived
```

| `kind` | Use it for | Generated RTL | Static latency |
|---|---|---|---|
| `level_sync` (alias `2ff_sync`) | A single-bit or one-hot level/status signal | `cdc_sync2ff` — double-flop, destination-clocked | Fixed +2 destination cycles |
| `pulse_sync` | A one-cycle source-domain event, with a declared minimum spacing between events | `cdc_pulse_sync` — toggle + double-flop-sync + edge-detect | Fixed +3 destination cycles |
| `mailbox_transfer` | A coherent multi-bit payload, one outstanding transfer at a time | `cdc_mailbox` — full request/acknowledge handshake | Unknown — depends on relative clock phase |
| `async_fifo` | A multi-bit stream needing real dual-clock buffering | `cdc_async_fifo` — Gray-code dual-pointer FIFO | Unknown — depends on relative write/read rates |
| `waived` | A crossing that is safe without a synchronizer (related clocks, a quasi-static configuration value); `reason:` says why | None — a direct wire | Unchanged |

`level_sync`'s double-flop pattern only guarantees per-bit metastability
safety, not a coherent multi-bit sample — use `mailbox_transfer` (single,
occasional values) or `async_fifo` (a real stream) for a bus that must
arrive coherent. A single `cdc:` declaration approves both the clock- and
reset-crossing for its connection.

Every kind is a data crossing declared on a `connections:` entry, wired
into `write_structural_verilog`'s generation exactly like
`register_stages`/`delay_cycles`/`boundary` already are (see
`forge.contracts.cdc`'s own module docstring). Both the source and
destination side are wired continuously — FORGE's structural `port_map`
wiring has no generic valid/ready concept — so `mailbox_transfer` and
`async_fifo` provide real buffering/synchronization but not true
producer/consumer backpressure yet. `async_fifo`'s generated RTL exposes
`full`/`empty`/`overflow_attempt`/`underflow_attempt` signals for a future
release to report on (release-plan §5 Decision B / slice 10.0C) — not
wired into anything yet this release.

### Reset synchronization: a domain property, not a connection

A reset crossing isn't data that happens to cross domains — it needs
asynchronous assertion and synchronous deassertion specifically, and it
isn't a wire between two modules. It's declared on the top-level
`reset_domains:` block instead:

```yaml
reset_domains:
  rst_slow:
    derived_from: ap_rst
    sync: reset_sync
```

`sync: reset_sync` (the only supported value) generates a real
`cdc_reset_sync` instance (async-assert, sync-deassert, 2-stage) for that
destination reset domain, clocked by whichever clock domain the domain's
own member instances resolve to. `derived_from` is required whenever
`sync` is set — a reset can't be synchronized without a declared source
domain. `clock_domains:`/`reset_domains:` entries without `sync` remain
exactly what they always were: purely descriptive (`derived_from`/
`ratio`), never auto-approving a crossing.

## What's deferred

- **A single module with two independent clocks** is still not modeled —
  that's what `clock_secondary`/`reset_secondary` would need, and they're
  still inert.
- **A real second physical top-level clock/reset port.** Every kind above
  works and is fully unit-tested today via synthetic, in-memory fixtures
  and real-plugin regression guards (`passthrough_demo`/`trigger_demo`/
  `vision_pipeline_demo` all declare a single domain and are unaffected) —
  but no plugin in this repo has yet declared a genuinely second clock
  domain end to end, through real HLS synthesis and `xsim`. That's
  release-plan slice 10.4's job, not this one.
- **Coherent flow control / backpressure** on `mailbox_transfer`/
  `async_fifo` connections (see above) — release-plan §5 Decision B /
  slice 10.0C.
- There is no timing-closure or CDC-verification-tool integration here —
  this is a structural, name-based check that a crossing was
  acknowledged and a real synchronizer generated, not a substitute for
  STA/CDC signoff tooling.

`forge/tests/test_cdc.py` covers the detection logic (including a
positive and negative fixture per kind),
`forge/tests/test_cdc_generation.py` covers the generated RTL text for
every kind (including the manifest's framework-support-RTL discovery),
`forge/tests/test_cdc_schema_loading.py` covers `design.yml`-level
parsing of `cdc:`/`clock_domains:`/`reset_domains:`, and
`forge/tests/test_cdc_validate_integration.py` covers the `forge dev topgen
validate` wiring end to end — all real, passing tests, not placeholders.
