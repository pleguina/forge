# Payload control policies

`forge dev framework emit-payload --policies <file>` reads a YAML file whose
`control_policies` map shapes the Blobfish `payload.v` around the generated
algorithm top. Without it, every resolved link is connected and every other
payload output is tied off. A file that cannot be loaded is an error.

```yaml
control_policies:
  algo_clk: {default: logic_clk}
  reset: {source: slr1_usr_rst_in, active: high, sync_stages: 2}
  tx: {register: true, orbit: gmt_orbit_dly_dout}
  input_register: true
  connect:
    bx_timing_bx0_global: {net: bx0_data, register: true, width: 1}
    bx_timing_bx0_phase_err: omtf_mon_bx0_phase_err
  drive:
    slr0_readback_registers_out: "{192'b0, status}"
    "slr0_gt_1_tx_tdata[64*0 +: 64]": {expr: "tap_r1[67:4]", comment: "RX tap: slr0_gt0_lane2"}
  rx_gate:
    rx_slr0_gt0_lane1: play_mask_0_1
  verilog:
    header: [payload_header.v]
    before_algo: [payload_before_algo.v]
```

## Algorithm reset

A payload with links must say what drives the algorithm's `ap_rst`; there is
no silent default.

| `reset:` | Effect |
|-----|--------|
| `{source: <port>, active: high\|low, sync_stages: N}` | The 1-bit ABI input `<port>` (inverted when `active: low`) through `N` registers in `logic_clk` (default 2). The registers start at 1, so the algorithm is held in reset from power-up until the first synchronized deassertion |
| `disabled` | `ap_rst` tied low: the algorithm has no reset, by decision |

## Links

A link whose endpoint protocol is `csp_*` carries its whole 67-bit word,
`{tdata, tlast, tfirst, tvalid}`, in both directions. The protocol comes from
the framework's endpoint manifest.

| Key | Effect |
|-----|--------|
| `input_register: true` | Every link word reaches the algorithm through one register, `lin_q_<algo port>` |
| `tx.register: true` | Every used TX lane's word leaves through a `DONT_TOUCH` register of its own, `<endpoint>_csp_q` |
| `tx.orbit: <port>` | That algorithm output drives `tx_orbit` of every used TX lane (registered per lane with `tx.register`). The ABI must have the quad's `tx_orbit` port |

## The payload's own logic

What the ABI and the resolved I/O do not describe, such as board control,
status words and test features, is written by the consumer and placed by
these keys. Nothing patches `payload.v` afterwards.

| Key | Effect |
|-----|--------|
| `connect` | Extra algorithm ports: `port: net`, or `port: {net, register: true, width}` to pass the net through an input register like the links |
| `drive` | Payload outputs, whole ports or port slices exactly as FORGE writes them, driven by an expression (or `{expr, comment}`). FORGE writes no tie-off for them. The assignments come after every inserted block |
| `rx_gate` | RX endpoint → mask net, ANDed with each of the lane's signals (`tdata` with the mask replicated) |
| `verilog.header` | Verilog files inserted after the port list, before the RX lanes |
| `verilog.before_algo` | Verilog files inserted after the TX lanes, before the input registers and the algorithm instance |

Relative `verilog` paths resolve against the policies file's directory.

## Framework signals

| Key | Effect |
|-----|--------|
| `algo_clk.default` | Clock assigned to every quad's `algo_clk` |
| `buffer_alignment.default` | `disabled_zero` ties the buffer increment and decrement off |
| `play_data` | `source: disabled_zero` or `slr_config_bit` (with `slr`, `bit` and an optional `gate`) for `play_data_out`, unless `drive` names the port |
| `port_aliases` | `"instance.port": algo_port` for an output whose algorithm port name is not `<instance>_<port>` |
