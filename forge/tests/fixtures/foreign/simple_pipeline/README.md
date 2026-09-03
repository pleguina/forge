# simple_pipeline

A small, deliberately un-FORGE-shaped DSP pipeline: three RTL blocks, one
clock, one active-low reset, an ordinary `rtl/` + `tb/` + `constraints/`
layout, and no FORGE configuration of any kind.

It exists to be adopted. Nothing in it may be reorganised, renamed or
annotated to make `forge adopt` easier — the moment it is, it stops testing
the thing it was created to test (the plan's Phase E, "stop validating FORGE
exclusively against designs created by FORGE's author").

    sample_in ─► decimator ─► shaper ─► packer ─► packet_out
