# daq_readout

Foreign-project corpus, Project C (plan §10, Phase E1).

A readout chain with the shape a real trigger/DAQ design has: several
instances of one module, fan-in to an aggregator, fan-out to two consumers,
an existing structural top, per-channel array naming, and a second clock
domain at the output boundary.

What it is here to test, none of which Projects A or B cover:

- **An existing structural top.** `readout_top` instantiates everything.
  Adoption must recognise it as the top level FORGE is *replacing*, keep it
  out of the managed module set, and read the instance counts off it —
  `channel_decoder` is instantiated four times, and `instances: 4` is a fact
  this file proves.
- **Fan-in with per-channel array naming.** Four decoders feed
  `hit_aggregator`'s `ch0_hit`..`ch3_hit`. FORGE must *not* invent that
  binding from names: an array binding is a modelling decision, and
  inventing one would silently wire channel 0's data to every channel.
- **Fan-out.** `hit_aggregator`'s output feeds both `trigger_path` and
  `monitor_path`. Two valid consumers for one producer is precisely the
  ambiguity that must be reported rather than resolved.
- **A second clock domain.** `readout_link` runs on `link_clk` while
  everything else runs on `sys_clk`.
- **Scale.** Enough modules and connections that a regression in discovery
  shows up as a wrong count rather than a crash.

Nothing here may be reorganised to make adoption easier.
