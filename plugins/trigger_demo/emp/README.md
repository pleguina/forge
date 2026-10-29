# trigger_demo as an EMP payload

The FORGE-generated `algo_top` of trigger_demo inside an
[EMP](https://gitlab.cern.ch/p2-xware/firmware/emp-fwk) payload, built by
ipbb and simulated with emp-fwk's own testbench. Nothing here is generated
by FORGE except `algo_top` and the ipbb component around it; the payload
and its dep files are what an EMP group writes by hand.

```
emp/
├── firmware/cfg/emp_payload.dep   ← the payload, for any EMP project
├── firmware/cfg/top.dep           ← emp-fwk's testbench around it
├── firmware/hdl/emp_payload.vhd   ← links 0-3 → the four hit decoders; trigger word → link 0
├── firmware/hdl/emp_project_decl.vhd, tb_decl.vhd
├── tools/emp_tools.py             ← golden events as pattern files, output and entity checks
└── run_emp_sim.sh
```

## Mapping

| EMP | algo_top |
|-----|----------|
| `clk_p` | `ap_clk` |
| `rst_loc(0)` | `ap_rst` |
| `d(i).data(31 downto 0)`, `d(i).valid`, i = 0..3 | `dec_i_raw_hit`, `dec_i_raw_valid` |
| `q(0).data(31 downto 0)` | `tout_trigger_word` |
| `q(0).valid`, `.start`, `.last` | `tout_out_valid` (each trigger word is a one-frame packet) |

IPbus, `bc0`, GPIO and S-Link outputs are tied off; the other output links
are `LWORD_NULL`.

## Running it

```bash
git clone -b v0.9.3 https://gitlab.cern.ch/p2-xware/firmware/emp-fwk.git
git clone -b v1.9 https://github.com/ipbus/ipbus-firmware.git
pip install -e forge/ "git+https://github.com/ipbus/ipbb.git@dev/2024b"
source <Vivado>/settings64.sh; source <Vitis_HLS>/settings64.sh

EMP_FWK=emp-fwk IPBUS_FW=ipbus-firmware plugins/trigger_demo/emp/run_emp_sim.sh
```

ipbb 2024b's vunit-hdl dependency needs `setuptools<70` and
`--no-build-isolation` on recent pip.

The script:

1. checks that `emp_payload`'s ports are emp-fwk's (`emp_tools.py check-entity`);
2. synthesises the four HLS modules and runs `forge build --mode vhdl`;
3. turns the build into an ipbb component, `trigger_demo_gen:algo`, with
   `forge dev framework ipbb-component`: `algo_top.dep` and every source
   `build_manifest.json` lists (the VHDL entity, `algo_top_core.v`, the HLS
   and hand-written modules, FORGE's helpers);
4. creates an ipbb work area with emp-fwk, ipbus-firmware, this repository
   (as `forge`) and that package, and runs `ipbb toolbox check-dep`;
5. writes the golden events (`trigger_demo_golden.xml`) as an EMP pattern
   file, one event per frame on links 0-3;
6. compiles the dep tree in XSim with `forge dev framework ipbb-xsim`
   (ipbb's own sim flow needs Questa), plays
   the pattern file through emp-fwk's testbench and checks the valid words
   captured on output link 0 against the golden trigger words.

For a board build, include `emp_payload.dep` from the board project's
top-level dep file in place of emp-fwk's `emp_simple_payload.dep`, with
`trigger_demo_gen` in the work area.
