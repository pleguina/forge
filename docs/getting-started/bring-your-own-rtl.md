# Bring Your Own RTL

[The quickstart](quickstart.md) starts from nothing: `forge init` creates a
project already shaped the way FORGE expects. This page starts from the
other end — a repository that exists, was laid out by someone who had never
heard of FORGE, and is not going to be reorganised.

You will not write a module registry, a design topology, an interface
contract or a verification manifest by hand. FORGE reads them out of the
sources you already have.

## The repository

Anything with this shape works. The names and the layout are yours:

```
simple_pipeline/
├── rtl/
│   ├── decimator.v
│   ├── shaper.v
│   └── packer.v
├── tb/
│   └── tb_pipeline.v
└── constraints/
    └── timing.xdc
```

Three blocks feeding each other, one clock, one active-low reset. If you
want to follow along with exactly this one, it ships with FORGE as
`forge/tests/fixtures/foreign/simple_pipeline`.

## 1. Adopt it

```bash
cd simple_pipeline
forge adopt .
```

```
Scanning project...

RTL
    3 verilog modules
Simulation
    1 candidate testbench files
Constraints
    1 constraint files

Detected clocks
  clk

Detected resets
  rst_n  (active low)

Inferred topology
  decimator.decimated  ->  shaper.decimated   [16 bit, inferred]
  decimator.decimated_valid  ->  shaper.decimated_valid   [1 bit, inferred]
  shaper.shaped  ->  packer.shaped   [16 bit, inferred]
  shaper.shaped_valid  ->  packer.shaped_valid   [1 bit, inferred]

Created:
  forge.yml
  .forge/project/modules.yml
  .forge/project/design.yml
  .forge/contracts/decimator.interface.yaml
  .forge/contracts/packer.interface.yaml
  .forge/contracts/shaper.interface.yaml
```

Nothing in `rtl/`, `tb/` or `constraints/` was touched. Two things were
created:

- **`forge.yml`** — yours. Fifteen lines, and the only file you maintain.
- **`.forge/`** — FORGE's. Generated from `forge.yml` and your sources, and
  safe to delete and regenerate at any time.

```yaml title="forge.yml"
project: simple_pipeline
sources:
  rtl:
    - rtl/*.v
part: xcvu9p-flga2104-2L-e
clock:
  port: clk
reset:
  port: rst_n
  active: low
```

### What adopt will and will not decide

`forge adopt` writes what your sources prove and what a convention resolves
unambiguously. It writes nothing where more than one reading is valid.

So the pipeline above is wired: `decimator.decimated` is 16 bits wide and
there is exactly one 16-bit input called `decimated` anywhere in the design.
But had there been two, you would have got this instead of a connection:

```
Unresolved decisions: 1
────────────────────────────────────────────────────────────
  [AMBIGUOUS] classifier.candidate_out has 2 equally valid consumers —
  width and direction match all of them and no semantic family separates
  them
      - formatter.candidate_in
      - debug_sink.candidate
```

That is the whole design principle: both candidates satisfy width and
direction, so a guess would produce a design that builds, simulates, and is
wrong. FORGE stops and asks.

### Answering the questions

You can answer by editing `forge.yml` — every question prints the exact edit
that settles it — or by having FORGE ask you:

```bash
forge adopt --interactive
```

```
1 decision(s) FORGE will not make for you.
Each answer is written to forge.yml, where you can change it later.

[1/1] classifier.candidate_out has 2 equally valid consumers — width and
direction match all of them and no semantic family separates them

  [1] debug_sink.candidate
  [2] formatter.candidate_in
  [s] skip — decide later

  Equivalent edit: Declare the intended consumer for
  classifier.candidate_out — add the pair under connections: in forge.yml,
  or answer it with `forge adopt --interactive`

> 2

Recorded 1 answer(s) in forge.yml:
  classifier.candidate_out -> formatter.candidate_in
```

What it wrote is an ordinary declaration:

```yaml
connections:
- from: classifier.candidate_out
  to: formatter.candidate_in
```

There is no interactive state anywhere: the answer lives in `forge.yml`,
adoption re-runs from that file, and a project answered interactively is
byte-identical to one where you typed those two lines yourself. Change your
mind by editing the entry; delete it and the question comes back.

Skipping is always available, and a question you skip is simply reported as
before. The same mechanism records the other decisions FORGE declines to
make — which clock drives the design (`clock.port`), which module is the top
level (`top:`), which function is an HLS kernel's top and whether a module
FORGE cannot model should be integrated as [opaque](#opaque-modules).

## 2. See where you stand

```bash
forge check
```

```
FORGE project status
────────────────────────────────────────────────────────────

  ✅ Sources                   PASS               3 RTL, 0 HLS, 3 modules
  ◐  Contracts                 PARTIAL            3 of 3 unreviewed
  ✅ Topology                  PASS               4 connections, 2 in / 2 out
  ⚠️  Clock/reset model         WARN               clock clk, reset rst_n (active low)
  ◐  Latency model             PARTIAL            0 of 3 declared
  ○  Generated artifacts       NOT CONFIGURED     not generated yet
  ○  Verification              NOT CONFIGURED     no dataset, no flow

  Project completion: 54% (in progress)
```

`forge check` is to your project what [`forge doctor`](quickstart.md) is to
your installation. Every blocker it reports comes with the step that clears
it, and `--json` gives you the same information in the
[standard envelope](../development/cli_exit_codes.md).

## 3. Do the next thing

```bash
forge next
```

```
Recommended next step
  Generate the structural top level

Reason
  Generated artifacts: not configured — not generated yet

Run
  forge build .forge/project/design.yml --contracts-from .forge/project/modules.yml \
      --consumer-root . --output .forge/generated/algo_top.v --apply
```

`forge next` is `forge check` narrowed to one recommendation, so you do not
have to hold the workflow in your head. Run what it prints:

```
  ✓ Structural top level: .forge/generated/algo_top.v
  ✓ Canonical IR snapshot: .forge/generated/design.ir.json
  ✓ Provenance manifest: .forge/generated/provenance.json
```

```verilog title=".forge/generated/algo_top.v (excerpt)"
module algo_top (
  input ap_clk,
  input ap_rst,
  input [15:0] decimator_sample_in,
  input decimator_sample_in_valid,
  output [31:0] packer_packet_out,
  output packer_packet_out_valid
);
  wire [15:0] net_decimator_decimated;
  ...
  shaper shaper (
    .clk(ap_clk),
    .rst_n(ap_rst),
    .decimated(net_decimator_decimated),
    .decimated_valid(net_decimator_decimated_valid),
    .shaped(net_shaper_shaped),
    .shaped_valid(net_shaper_shaped_valid)
  );
```

Then `forge check` again and watch the completion figure move.

## Ask why

Nothing above should be a black box. Point `forge explain` at any decision
and get the account back:

```bash
forge explain shaper.shaped
```

```
shaper.shaped — output, 16 bit

HDL
  output [15:0] shaped
  in rtl/shaper.v

Contract
  role: shaped
  no wiring_kind, protocol or coordinates declared — the semantics FORGE
  cannot infer are still unset

Resolved connection
  packer.shaped

Why
  wiring method: port_map
  width: 16 -> 16

Evidence
  port_direction = output  [deterministic, from rtl/shaper.v]
  port_width = 16  [deterministic, from rtl/shaper.v]
  wiring_method = port_map  [deterministic, from canonical IR]

Source: canonical IR
```

That last line matters. **canonical IR** means this is what the generators
actually consumed — resolution is a pure function of your configuration, so
it works from the moment `forge adopt` has run, with no build needed. If a
design does not resolve, `forge explain` falls back to what discovery
inferred and says so rather than presenting the two as the same thing.

A refusal is explained just as carefully as a decision. Ask about the
ambiguous producer from earlier and you get every candidate FORGE weighed,
why none of them won, and how to settle it. Other things to point it at:

```bash
forge explain shaper                 # a module: source, ports, contract, connections
forge explain clock:clk              # why a port name reads as the clock
forge explain reset:rst_n            # and a reset, including its active level
forge explain connection:a.q->b.d    # full matching evidence, rejected candidates included
forge explain hls:regression         # an HLS kernel's predicted RTL interface
forge explain ATG037                 # a diagnostic code, and where it fired here
forge explain artifact:.forge/generated/algo_top.v
```

## Let FORGE do the mechanical parts

Some of what `forge check` reports has exactly one right answer. If a
contract says a port is 31 bits and the RTL says 32, nobody needs to
adjudicate that:

```bash
forge fix
```

```
Available: 1 automatic fix(es).

1. shaper.interface.yaml: 1 width(s) contradict shaper.v
   -      width: 31
   +      width: 16
   Evidence:
     width_declared_by_source = shaped: 31 -> 16  [deterministic, from rtl/shaper.v]

Needs you — these are decisions, not transcription:
  - Review the 3 draft contract(s) under .forge/contracts and mark them ready
  - Confirm how 'rst_n' is driven — FORGE does not model reset polarity

Run `forge fix --apply` to apply them.
```

It previews as a diff, with the evidence, and writes nothing until
`--apply`. And it stops firmly where transcription ends: assigning a
semantic family or picking one of two valid consumers is a decision about
your intent, so `forge fix` will not make it — not even behind a
confirmation prompt. Those stay on the "needs you" list.

## Upgrading FORGE later

When you update FORGE, the question is whether your project still works.
`forge migrate` answers it:

```bash
forge migrate
```

It finds every schema-bearing file — in this layout and in the older
`plugins/<id>/forge/` one — works out what each needs, and shows you the
whole chain as one diff before touching anything:

```
Project schema migration — /path/to/simple_pipeline

This FORGE writes
  forge.yml    1.0
  design       1.0
  registry     1.0
  interface    1.0

Required changes
  none — every file already declares a schema this build understands
```

Dry-run by default, `--apply` to write, and idempotent — running it twice is
running it once. A migration that *moves files* is reported with the command
that performs it rather than done by a preview command.

## What FORGE told you about your design

Two of the lines above are worth reading closely, because they are FORGE
being honest about its own limits rather than about your code:

**`Clock/reset model — WARN`.** Your reset is active-low. FORGE's generators
collapse every module reset onto one top-level `ap_rst` net and do not model
its polarity, so `rst_n` is wired straight through. You need to drive
`ap_rst` with the polarity your modules expect. Better to learn that here
than from a waveform.

**`Latency model — PARTIAL`.** A module's pipeline depth is not visible in
its port list, so adoption never invents one. Until you declare it, the
static latency check has nothing to align. Add it to
`.forge/project/modules.yml`:

```yaml
- name: shaper
  latency:
    kind: fixed
    cycles: 1
```

If your repository contains something FORGE cannot manage at all — a vendor
IP with three functional clock domains, say — you are told during adoption,
with the ways forward, and the module is left out of the generated project
rather than half-integrated. Its clocks are still *reported*, because they
are real, but they will not configure a design the module is no longer part
of:

```
  [UNSUPPORTED] axi_bridge: 3 clock inputs (m_axi_aclk, ref_clk, s_axi_aclk)
  — FORGE manages one functional clock domain per module
      - import axi_bridge as an opaque module (structure only): add
        `axi_bridge: {management: opaque}` under `modules:` in forge.yml
        and re-run `forge adopt`
      - split axi_bridge into one FORGE-managed wrapper per clock domain
      - exclude axi_bridge from the FORGE-managed topology
```

### Opaque modules

The first of those is usually the answer, and it is the one FORGE can act
on. Vendor IP, encrypted blocks, an existing top-level you are not going to
restructure — FORGE integrates them *structurally* without claiming to
understand their internals. Say so in `forge.yml`:

```yaml
modules:
  axi_bridge:
    management: opaque
```

Re-run `forge adopt`, and the module joins the design. Its clock and reset
pins other than your design's own are routed to the generated top level for
the enclosing design to drive:

```verilog
module algo_top (
  input ap_clk,
  input ap_rst,
  ...
  input axi_bridge_m_axi_aclk,
  input axi_bridge_ref_clk,
  input axi_bridge_s_axi_aclk,
  input axi_bridge_s_axi_aresetn,
  ...
);
  axi_bridge axi_bridge (
    .m_axi_aclk(axi_bridge_m_axi_aclk),
    .ref_clk(axi_bridge_ref_clk),
    .s_axi_aclk(axi_bridge_s_axi_aclk),
    ...
  );
```

That routing is the entire point. Tying three functional clocks to one
`ap_clk` net produces a top level that elaborates perfectly and is silently,
catastrophically wrong, so FORGE would rather leave the module out than do
it — and opaque is how you tell it that you have the domains covered.

`forge check` then reports the module as handled rather than as an open
question:

```
  ✅ Supported constructs      PASS               1 opaque: axi_bridge
```

## Where to go next

Everything under `.forge/` is ordinary FORGE configuration — the same
format a hand-authored project uses, which is why `forge topgen validate`,
`forge inspect`, `forge report` and the rest work on an adopted project
unchanged. When you need more than adoption can infer, that is where you
edit:

- [Contracts and protocols](../concepts/contracts-and-protocols.md) — the
  semantics adoption leaves blank (`wiring_kind`, `protocol`, coordinates)
  and when you need them.
- [Author topology contracts](../how-to/author-topology-contracts.md) —
  topology groups, scatter/gather, arrays.
- [Latency model](../concepts/latency-model.md) — declaring stage depths so
  the static check can align your pipeline.
- [Integrate verification](../how-to/integrate-verification.md) — the last
  `NOT CONFIGURED` line in your report.
- [Diagnostic catalogue](../reference/diagnostics.md) — every code
  `forge check` can report, and what each one means.
