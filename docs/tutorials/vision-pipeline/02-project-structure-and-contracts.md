# 02 — Project structure and contracts

**Step in this chapter:** *(none — reading chapter, revisits `quickstart`)* · **Tools needed:** Python

## Goal

Understand the three files that make every design in this tutorial
work — the module registry, an interface contract, and a design
topology — before chapter 03 adds a second module.

## What you will learn

- What `modules.yml` registers and why every module needs a `latency:` declaration.
- What an interface contract (`*.interface.yaml`) actually describes.
- How `design.yml` turns a registry entry into a real instance.

## Starting design

`design.yml` (from chapter 01) — no new design this chapter.

## What you add

Nothing runnable. This chapter is about reading, not building.

## Files you edit

You don't edit anything in this chapter, but you should open:

| File | Ownership | What it is |
|---|---|---|
| `plugins/vision_pipeline_demo/forge/modules.yml` | **PROJECT SOURCE** | The module registry — every HLS/RTL module this project can instantiate, in one shared file. |
| `plugins/vision_pipeline_demo/forge/interfaces/pixel_normalizer_ip.interface.yaml` | **PROJECT SOURCE** | `pixel_normalizer`'s interface contract. |
| `plugins/vision_pipeline_demo/forge/designs/design.yml` | **PROJECT SOURCE** | The quickstart topology from chapter 01. |

## What FORGE generates

Nothing new — `forge topgen validate-registry`/`validate` only read
these files, they don't write anything.

## Command to run

```bash
forge topgen validate-registry plugins/vision_pipeline_demo/forge/modules.yml
forge topgen validate          plugins/vision_pipeline_demo/forge/designs/design.yml
```

## Expected terminal result

Both commands print a validation summary with zero errors. `modules.yml`
currently registers 19 modules (3 HLS, 16 RTL) shared across every
design in this tutorial — `design.yml` itself only instantiates two of
them (`pixel_normalizer` as `norm`, `threshold_rtl` as `thresh`).

## Artifacts to inspect

**The module registry** (`modules.yml`) — one entry per module:

```yaml
- name: pixel_normalizer
  kind: hls
  top: pixel_normalizer
  latency:
    kind: fixed
    cycles: 3
  interface_contract: interfaces/pixel_normalizer_ip.interface.yaml
  src: [../algo/normalizer/pixel_normalizer.cpp]
```

`latency:` is not optional decoration — `forge analyze latency-check`
(chapter 04) reads it to verify that parallel paths reconverge on the
exact cycle they're supposed to. `kind: fixed`/`bounded`/`elastic` are
the three latency shapes this tutorial covers (chapters 01, 05).

**The interface contract** — what a "role" actually is: a named,
directioned, fixed-width port with a stable identity independent of the
module's own raw Verilog/HLS port name. `pixel_normalizer`'s contract
declares 16 roles, including the two every module needs:

```yaml
roles:
  clock_primary:
    raw_port: ap_clk
    direction: input
    width: 1
  reset_primary:
    raw_port: ap_rst
    direction: input
    width: 1
    active_level: high
  # ...14 more data roles
```

`clock_primary`/`reset_primary`'s `raw_port` name is what FORGE's clock
and reset binding actually keys on — see chapter 06 for what happens
when two modules disagree about which port is `ap_clk`.

**The design topology** (`design.yml`) — turns a registry entry into a
named instance and wires instances together via `port_map`:

```yaml
registry: ../modules.yml
modules:
  - name: norm
    ref: pixel_normalizer
  - name: thresh
    ref: threshold_rtl
connections:
  - from: norm
    to: thresh
    port_map:
      - [normalized_pixel, in_pixel]
      - [normalized_valid, in_valid]
```

`ref:` looks up the registry entry; `name:` is this design's own local
instance name (what generated port names get prefixed with — recall
`norm_x`/`thresh_out_pixel` from chapter 01).

## Visual result

Not applicable — this chapter has no new topology to render.

## Why the capability matters

Every design from here on is the same three ingredients — registry,
contracts, topology — just with more instances and more connections.
Understanding the shape now means chapter 03 onward reads as "what's
different," not "what is this file."

## Common failure

A design that references a `ref:` not present in the registry, or an
instance's `port_map` naming a role the contract doesn't declare, fails
`forge topgen validate` with a clear diagnostic — see
[chapter 12](12-diagnostics-and-negative-fixtures.md) for real examples
of validation catching a structural mistake before any RTL is generated.

## What changed from the previous chapter

Nothing built — this chapter is the reference material chapter 01's
commands were already exercising.

## Next chapter

[03 — Mixed RTL/HLS](03-mixed-rtl-hls.md)
