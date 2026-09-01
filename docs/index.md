# FORGE

Framework for Orchestrated RTL Generation & Evaluation.

> An algorithm-agnostic, contract-driven integration and verification
> framework for RTL and HLS firmware targeting **AMD/Xilinx** FPGA
> toolchains.

FORGE is a plugin-agnostic framework for contract-driven DUT generation,
optional HLS orchestration, and reusable verification runtime flows. It
originated as trigger/DAQ firmware tooling built at CERN for the CMS/OMTF
collaboration, and is released as a general-purpose, detector-agnostic
open-source project (MIT-licensed) — CMS/OMTF remains a user of the
framework, not its scope. See
[Contributing](development/contributing.md) for the provenance note and
current distribution story (git-install-only, no PyPI publish — a closed
decision, not a placeholder pending one).

## Current scope

FORGE's public identity for this release is deliberately narrower than
"universal FPGA/ASIC framework." What's actually implemented and tested:

| Axis | Current support |
|---|---|
| Toolchains | Vivado (structural Verilog/VHDL, Block Design TCL), Vitis HLS, XSim |
| Simulation backends | `xsim`, `csim`; Verilator is available as a lint tool only, not a simulation backend |
| Verification datasets | XML-backed only (`design.verification.yml` + XML golden data) |
| Clock/reset domains | A single functional clock/reset domain per module — `clock_secondary`/`reset_secondary` roles exist in the schema but are explicitly **reserved** and have no functional effect. Real, structural cross-module CDC crossing detection and synchronizer generation *do* exist, gated behind `gen-top --strict` (see [Clock and Reset Domains](concepts/clock-and-reset-domains.md)) |
| Topology matching | Contract-driven wiring, scatter/gather, N-D template and prefix-array bindings, structured `coordinates:`/legacy `partition:` matching (see [Contracts and Protocols](concepts/contracts-and-protocols.md)) |
| Vendors/toolchains **not** supported | Intel/Altera, Lattice, generic ASIC flows, GHDL or cocotb/VUnit as a primary simulation path |

For the architectural rationale behind the canonical IR and how generation
is driven by it end-to-end, see
[Architecture Rationale](explanation/architecture-rationale.md).

## Where to go next

- Want the whole picture first? [What FORGE Does](capabilities.md) tours
  every capability on one page and links into the detail.
- New to FORGE? Start with the [Quickstart](getting-started/quickstart.md)
  to get from zero to a working environment.
- Want a guided, end-to-end walkthrough? Follow the
  [Golden-Path Tutorial](tutorials/golden-path.md).
- Adding a new module or plugin? Read
  [How to Author Topology Contracts](how-to/author-topology-contracts.md).
- Want to understand the contract model underneath topology generation?
  See [Contracts and Protocols](concepts/contracts-and-protocols.md).
- Looking for a specific CLI command or flag? See the
  [CLI Reference](reference/cli.md).
- Curious what's in scope versus explicitly deferred for this release? See
  [Project Scope](explanation/project-scope.md).
