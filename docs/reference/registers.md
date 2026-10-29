# Register maps

A board's control and status registers are declared once, in a YAML register
map, and exported for each framework by
`forge dev framework registers`:

```bash
forge dev framework registers --map registers.yml \
  --ipbus address_table.xml \
  --blobfish register_policies.yml --slr 1 \
  --doc registers.md
```

```yaml
registers:
  - name: control
    kind: config            # config | status | counter | snapshot | latency_monitor
    width: 64
    reset: 0
    clock_domain: logic_clk
    port: cfg_control       # config: the algorithm input it drives
    fields:
      - {name: enable, bits: 0}
      - {name: mode, bits: [3, 1], description: "readout mode"}
  - name: lock
    kind: status
    width: 32
    net: lock_status        # read-only kinds: the payload net read back
    fields:
      - {name: locked, bits: 0}
  - name: drops
    kind: counter
    width: 32
    net: drop_count
    clear_on_read: true
```

| Key | Meaning |
|-----|---------|
| `kind` | `config` is read-write and drives `port`; the others are read-only and read `net` |
| `width`, `reset` | Bits, and the value after reset (must fit) |
| `fields` | Named bit ranges; overlapping fields, or fields outside the register, are errors |
| `clear_on_read` | Counters only: a read returns the value and clears it |
| `atomic` | A register wider than the bus word that must be read in one piece; an export whose bus reads less at a time refuses it |
| `clock_domain` | Documentation of the domain the register lives in |

## Exports

| Option | Writes |
|--------|--------|
| `--ipbus` | A uHAL address table: one node per 32-bit word (`<name>_w<k>` for wider registers), permission `rw` or `r`, fields as masked child nodes. A field across a 32-bit word is an error |
| `--blobfish --slr N` | The `control_policies` that place the map in SLR N's 64-bit words ([payload policies](payload-policies.md)): each config register takes the next `config_words` word of `slrN_config_registers_in`, each read-only register the next 64-bit word of `slrN_readback_registers_out` through `status_words`, zero-padded. One register is at most one word |
| `--doc` | A Markdown table of words, kinds, access, widths, reset values, clock domains and fields |

FORGE does not access hardware: uHAL, SWATCH and the Blobfish control
software read these exports. `forge.integration.registers.FakeBus` gives a
map's read/write semantics (read-only registers, clear-on-read counters,
reset values) to tests of the software that uses them.
