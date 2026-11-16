# trigger_demo as an EMP payload

The FORGE-generated `algo_top` of trigger_demo inside an
[EMP](https://gitlab.cern.ch/p2-xware/firmware/emp-fwk) payload, built by
ipbb and simulated with emp-fwk's own testbench. FORGE generates the
algorithm, its ipbb component and `emp_payload.vhd`; the mapping between links
and algorithm ports (`emp_payload.yml`), the project declarations and the dep
files are written by hand.

```
emp/
├── emp_payload.yml                ← links 0-3 → the four hit decoders; trigger word → link 0
├── firmware/cfg/emp_payload.dep   ← the payload, for any EMP project
├── firmware/cfg/top.dep           ← emp-fwk's testbench around it
├── firmware/hdl/emp_project_decl.vhd, tb_decl.vhd
├── tools/emp_tools.py             ← golden events as pattern files, output check
└── run_emp_sim.sh
```

## The payload

`forge dev framework emp-payload` copies the `emp_payload` entity from the
emp-fwk checkout the project uses, so its ports are that release's (`bc0` up
to 0.9.x; `ttc2`, `tts_busy` and `ext_lvds_*` from 0.10), and writes the
architecture from `emp_payload.yml`:

| EMP | algo_top |
|-----|----------|
| `clk_p` | `ap_clk` |
| `rst_loc(0)` | `ap_rst` |
| `d(i).data(31 downto 0)`, `d(i).valid`, i = 0..3 | `dec_i_raw_hit`, `dec_i_raw_valid` |
| `q(0).data(31 downto 0)` | `tout_trigger_word` |
| `q(0).valid`, `.start`, `.last` | `tout_out_valid` (each trigger word is a one-frame packet) |

The other output links carry `LWORD_NULL`; the other payload outputs are tied
off by type. A port of a type FORGE has no tie-off for is an error until the
spec drives it.

## Running it

```bash
git clone -b v0.10.3 https://gitlab.cern.ch/p2-xware/firmware/emp-fwk.git
git clone -b v0_2_1 https://gitlab.cern.ch/cms-tcds/cms-tcds2-firmware.git   # emp-fwk 0.10 only
git clone https://gitlab.cern.ch/jhegeman/tclink.git && git -C tclink checkout 4591a4fe5312   # emp-fwk 0.10 only
git clone -b v1.9 https://github.com/ipbus/ipbus-firmware.git
pip install -e forge/ "git+https://github.com/ipbus/ipbb.git@dev/2024b"
source <Vivado>/settings64.sh; source <Vitis_HLS>/settings64.sh

EMP_FWK=emp-fwk IPBUS_FW=ipbus-firmware EMP_DEPS="cms-tcds2-firmware tclink" \
  plugins/trigger_demo/emp/run_emp_sim.sh
```

It runs the same way against emp-fwk v0.9.3, without `EMP_DEPS`. ipbb 2024b's
vunit-hdl dependency needs `setuptools<70` and `--no-build-isolation` on
recent pip.

The script:

1. synthesises the four HLS modules and runs `forge build --mode vhdl`;
2. turns the build into an ipbb component, `trigger_demo_gen:algo`, with
   `forge dev framework ipbb-component`, and writes
   `trigger_demo_gen:payload`'s `emp_payload.vhd` with
   `forge dev framework emp-payload` for the given emp-fwk;
3. creates an ipbb work area with emp-fwk, ipbus-firmware, the `EMP_DEPS`,
   this repository (as `forge`) and that package, and runs
   `ipbb toolbox check-dep`;
4. writes the golden events (`trigger_demo_golden.xml`) as an EMP pattern
   file, one event per frame on links 0-3;
5. compiles the dep tree in XSim with `forge dev framework ipbb-xsim`
   (ipbb's own sim flow needs Questa), plays the pattern file through
   emp-fwk's testbench (with the Serenity KU115 `so1` board packages, as
   emp-fwk's own testbench project does) and checks the valid words captured
   on output link 0 against the golden trigger words.

For a board build, include `emp_payload.dep` from the board project's
top-level dep file in place of emp-fwk's `emp_simple_payload.dep`, with
`trigger_demo_gen` in the work area.
