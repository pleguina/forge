# ADR 0002: CDC primitive semantics in `vision_pipeline_demo`

## Status

Accepted.

## Context

`design_cdc.yml` and `design_platform_wrapper.yml` exercise all five
FORGE CDC primitives (`level_sync`, `pulse_sync`, `mailbox_transfer`,
`async_fifo`, `reset_sync`) across three genuinely independent clock
domains (control/pixel/output). FORGE's structural port-map wiring has
no generic valid/ready or empty/full concept at a crossing boundary —
the destination side of any crossing simply sees a continuously-driven
signal. Every module that consumes a crossing therefore needs its own
convention for detecting *when* that signal actually carries a new
value, and the convention must be consistent across the plugin so a
reader only has to learn it once.

## Decision

1. **Continuously-driven, self-describing payloads.** A crossing's
   destination sees its source value on every cycle, not just when a new
   value arrives. Where "did this actually change" matters (e.g. a
   record crossing an `async_fifo` into `packetizer_rtl`), the source
   side embeds a toggle bit inside the payload itself, alongside the
   real data. The destination detects novelty by comparing the toggle
   bit against its last-seen value — flips exactly once per real
   dequeued entry, holds steady otherwise. This needed no core-framework
   change; it reuses the same "continuously driven, self-describing
   payload" model every CDC primitive in this repo already relies on.
2. **New clock domains are declared implicitly, by a raw port name.**
   Verilog port names can't be parametrized, so a genuinely new
   top-level clock is introduced simply by having one module instance
   somewhere whose `interface_contract` declares a `clock_primary`
   `raw_port` name that isn't already in use — no separate "clock
   domain" declaration exists independent of that. Reusing a module
   whose contract hardcodes `ap_clk`/`ap_rst` as a *different* logical
   domain than another reused module's own `ap_clk`/`ap_rst` silently
   collapses both onto the same physical net; each domain needs its own
   distinctly-named clock/reset pair somewhere in the design.
3. **Non-primary reset domains synchronize from one shared external
   reset**, via `reset_domains: <domain>.sync: reset_sync` in the design
   YAML — a real `cdc_reset_sync` instance, not testbench-driven,
   synchronizing the same external reset into each secondary domain.
   `reset_domains:` only handles *this* synchronization, not clock
   generation itself (see point 2).

## Consequences

- Any new module that consumes a CDC crossing in this plugin should
  follow the toggle-in-payload convention rather than inventing a new
  novelty-detection scheme.
- Any new design that adds a clock domain should introduce it via one
  module's `interface_contract` clock name, and declare its reset
  synchronization explicitly via `reset_domains`, rather than assuming
  FORGE infers domain membership from anywhere else.
- `forge.analysis.latency_static`'s merge-point checker must treat a CDC
  edge's latency as genuinely unknown, not zero — an edge whose latency
  is `None` because it crosses domains is not the same as an edge that
  adds zero cycles. Conflating the two silently corrupts alignment math
  for any downstream merge point reached through a CDC crossing.
