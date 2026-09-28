# peripheral_subsystem

Foreign-project corpus, Project B (plan §10, Phase E1).

A peripheral subsystem laid out the way an ordinary FPGA repository is:
sources split across several directories, a VHDL/Verilog mix, a Makefile and
a Vivado TCL script, simulation under `sim/`, and a vendor IP wrapper whose
internals FORGE cannot and should not model.

What it is here to test, none of which Project A covers:

- **Several source directories.** `src/rtl`, `src/common` and `src/vendor`
  must all be found, and the generated `forge.yml` must name them
  individually rather than collapsing to one repository-wide `**` glob.
- **A build system in the tree.** `Makefile` and `scripts/build.tcl` must be
  recognised as build files, not mistaken for design sources.
- **A VHDL/Verilog mix.** Entities and modules from both languages must land
  in one module registry.
- **A vendor IP boundary.** `axi_bridge` has three functional clocks. FORGE
  manages one per module, so it must be reported as outside the envelope
  *during adoption*, with the ways forward — and left out of the generated
  project rather than half-integrated.
- **Naming that owes FORGE nothing.** `s_axi_aclk`, `pclk`, `presetn`,
  `wr_en` — conventional bus naming, not FORGE vocabulary.

Nothing here may be reorganised to make adoption easier.
