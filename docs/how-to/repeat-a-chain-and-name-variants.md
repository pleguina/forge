# Repeat a chain and name variants

A payload of many sectors repeats the same blocks and wiring per sector, and
is built several ways: for the board, for simulation, for debugging. Writing
each of them out copies connection tables that then drift apart. A design
defines the chain once as a **subsystem**, and `forge.yml` names each
**variant** as the changes it makes.

## A subsystem

```yaml title="design.yml"
subsystems:
  lane:
    modules:
    - {name: pre,  ref: scale, parameters: {SHIFT: 1, W: 12}, external_in_ports: [x, x_valid]}
    - {name: post, ref: scale, parameters: {SHIFT: 2, W: 12}, external_out_ports: [y, y_valid]}
    connections:
    - {from: pre, to: post, port_map: [[y, x], [y_valid, x_valid]]}

modules:
- name: lane
  subsystem: lane
  instances: 3
  overrides:                                   # exceptions, by copy
    1: {post: {parameters: {SHIFT: 0, W: 12}}} # fields replace the definition's
- {name: merger, ref: merger}

connections:
- {from: lane.post, to: merger, port_map: [[y, "in_{i}"]]}   # once per copy
- {from: "lane[1].post", to: merger, port_map: [[y_valid, sel]]}
```

Each copy's blocks are ordinary blocks named `<copy>__<block>`: `lane_0__pre`,
`lane_0__post`, `lane_1__pre`, ... (`lane__pre` when there is one copy).
Those are the names in the generated top, the IR, reports and verification
targets, and a copy keeps its name when the count grows.

| In a subsystem use | Means |
|---|---|
| `instances: N` | N copies |
| `overrides: {k: {block: {field: value}}}` | in copy k, these fields of the block replace the definition's: another `ref` replaces its implementation |
| `overrides: {k: {block: null}}` | copy k has no such block, and none of its connections |
| `from: lane.post` | a connection made once per copy, `{i}` replaced by the copy's index anywhere in it |
| `from: "lane[1].post"` | the block of one copy |

## Variants

```yaml title="forge.yml"
variants:
  board: designs/design.board.yml          # a design written with variant_of:
  sim:                                     # or the changes, applied to the project's design
    remove: {modules: [link_monitor]}
    modules:
      lane: {instances: 2, overrides: {1: {post: {ref: scale_model}}}}
```

The changes take the keys of a design written with `variant_of:`: `remove`
(modules, connections, topology groups), `modules` (fields of a block or a
subsystem use, `null` removing one), `add`, and any top-level key. They are
applied before subsystems are expanded, so a variant changes a count or adds
an exception without repeating anything else.

```bash
forge build --variant sim               # into .forge/variants/sim/generated/
forge inspect --variant sim --impact    # what it changes from the project's build
```

A verification target simulates a variant with `variant:`; `forge test`
generates it first:

```yaml title="forge.yml"
verification:
  targets:
    sim_payload: {variant: sim, input: data/in.json, expected: data/exp.json}
    post1: {scope: "instance:lane_1__post", replay: sim_payload}
```

[Change a processing stage](change-a-processing-stage.md) shows what
`--impact` reports.
