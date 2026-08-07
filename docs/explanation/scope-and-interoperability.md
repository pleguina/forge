# Scope and Interoperability

*Reviewed: 2026-07-29*

This page states what FORGE currently supports, on stable comparison
dimensions, and where it sits relative to a small number of other tools in
the same general space — RTL/HLS verification and topology integration.
It is deliberately not a competitive scorecard: no dimension here carries
a performance claim without reproducible evidence in this repository, and
every classification below uses the same supported/experimental/planned/
unsupported vocabulary as `docs/SUPPORT_CLASSIFICATION.md`
(now `docs/reference/support-classification.md`) and `CONTRIBUTING.md`. If
a comparison point isn't something we could verify precisely and
publicly, it's marked **not compared** rather than guessed at.

## FORGE's own scope, as of this review

Sourced directly from root `README.md`'s "Current scope" table — repeated
here, not re-derived, so the two never drift apart:

| Dimension | FORGE |
|---|---|
| Target toolchain | AMD/Xilinx only — Vivado (structural Verilog/VHDL, Block Design TCL), Vitis HLS |
| Simulation backends | `xsim` (supported), `verilator` (supported for `single_module_rtl`/`full_chip_rtl`), `csim` (HLS C-sim, supported). GHDL and cocotb/VUnit are **not supported** as a primary simulation path |
| HDL vs HLS support | Both — hand-written RTL (Verilog/VHDL) and Vitis HLS-generated modules, wired together in one topology |
| Verification dataset format | XML-backed (`design.verification.yml` + XML golden data); a JSON layer-A loader exists but the documented v1.0 contract is XML-first |
| Topology-generation approach | Contract-first (interface-contract-driven wiring), with heuristic name-matching as a compatibility fallback — see [architecture rationale](architecture-rationale.md) |
| Clock/reset domains | One functional clock/reset per module; real structural cross-module CDC-crossing detection exists under `--strict`, but no full multi-clock-domain-per-module model — see [clock and reset domains](../concepts/clock-and-reset-domains.md) |
| Vendors/toolchains not supported | Intel/Altera, Lattice, generic ASIC flows |

## Comparison points

Two widely-known tools occupy adjacent but distinct ground. Both
comparisons below are limited to structural, publicly-documented facts
about each tool's own stated purpose — not benchmarked against FORGE in
this repository, and not scored against it.

| Dimension | FORGE | cocotb | UVM |
|---|---|---|---|
| What it is | A contract-driven topology-generation and verification-orchestration framework | A Python-based cosimulation/verification library that drives an HDL simulator over its standard procedural interface (VPI/VHPI/FLI) | A SystemVerilog class-library methodology for constrained-random, coverage-driven verification |
| Simulator/toolchain scope | AMD/Xilinx-specific by design (Vivado/XSim, Vitis HLS) | Simulator-agnostic by design — targets whichever simulator exposes the interface it uses | Simulator-agnostic — any SystemVerilog-capable simulator |
| Topology generation | Yes — generates structural top-level RTL/VHDL/Block-Design TCL from a declared design topology | Not in scope — cocotb tests an existing DUT, it doesn't generate one | Not in scope — UVM structures testbenches, it doesn't generate DUT top levels |
| Verification style | Golden-dataset (XML) replay against declared flows, classified supported/experimental/unsupported per `(kind, backend)` | Directed or randomized Python testbenches, written per-project | Constrained-random, coverage-driven, written per-project in SystemVerilog |
| Performance/throughput | Not compared — no reproducible cross-tool benchmark exists in this repository | Not compared | Not compared |

## What this page is not

It is not a claim that FORGE is a substitute for cocotb or UVM in
general — they solve different problems (cosimulation-library and
verification-methodology, respectively, versus FORGE's
contract-driven-generation-plus-orchestration scope), and a project could
reasonably use FORGE for topology generation and one of these for
testbench methodology within a flow FORGE orchestrates. This page exists
to keep that boundary honest, not to rank the tools against each other.
