# Support Classification

Every FORGE capability is classified twice, on independent axes:

- **Audience.** *Task*: a supported user operation (`forge init`, `check`,
  `build`, `test`, `report`, `inspect`). *Expert*: a stage-level operation
  under `forge dev` or a Python API, for provider and extension authors.
- **Evidence.** *Tested*: covered by the test suite, against the named tool
  versions where a tool is involved. *Experimental*: implemented, without
  the validation a result would need to be trusted. *Unsupported*: outside
  the product; import another tool's result instead.

An expert primitive can be tested and stable; a task-level command can be
incomplete and says so in its output. Neither where a command sits in the
CLI nor how its report looks establishes that a result is correct.

## Inventory

| Capability | Audience | Evidence |
|---|---|---|
| `forge init`: forge.yml for existing firmware | Task | Tested |
| `forge init --example`: example plugin scaffold | Task (extension authors) | Tested |
| Existing top simulated against reference data (`verification.target: existing`) | Task | Tested: XSim 2024.1, Verilator 5.022 |
| Source descriptions: `.f`, Hog list files, ipbb dep trees (`verification.sources`) | Task | Tested for `.f` and Hog (XSim, mixed VHDL-2008/SystemVerilog); ipbb through ipbb's own parser, tested with a stub parser |
| Comparison: strict, `order: event` by value, `order: event` with a latency window, fields and codecs, framing | Task | Tested |
| Event association from pattern-file packets, or from the frames of a CMSSW `BoardDataWriter` sidecar | Task | Tested; the layout against every packet of `L1GTEvaluationProducer`'s input files from CMSSW_20_0_0 (TMUX 6 and 18 on one board, a file split) |
| `forge/integration/cmssw_sidecar.py` | Task (run in the CMSSW environment) | Tested |
| Focused runs (`--event-id`, `--pre-roll`) | Task | Tested |
| Evidence applicability (compile, run, comparison parts; memory, decoder, constraints files; simulator version) | Task | Tested |
| Board capture comparison (`forge test --capture`), firmware identity from a readback file | Task | Tested on captures written by the suite; no board was reachable |
| Implementation results from a Vivado timing summary (`--implementation`) | Task | Tested on report text |
| Replay and failure packages (`forge report --package`) | Task | Tested |
| Structural generation (`forge build`), subsystems, variants | Task | Tested |
| Latency alignment at declared latencies, `delay_cycles: auto`, refusal of a known misalignment | Task | Tested; declared latencies only, not post-route timing |
| Change impact (`forge inspect --impact`) | Task | Tested; static and conservative, unknown timing listed |
| Clock-domain checks of the declared model | Task | Tested; structural only, never CDC signoff |
| EMP payload wrapper, Blobfish payload, ipbb component export | Expert (provider) | Tested on entity declarations and manifests in the suite; a framework release is supported once its own build of the wrapper has run |
| Register maps, link maps | Expert | Tested |
| Raw topgen, analyze and verify stages (`forge dev ...`) | Expert | Tested |
| HLS Tcl generation, runs and port prediction (`forge dev hls`) | Expert | Tested; the port predictor per Vitis version, an untested version reported as such |
| `hls_cosim` verification kind | Expert | A legacy name for HLS C simulation; not C/RTL co-simulation |
| SLR assignment proposals and floorplan search | Expert | Experimental |
| Retiming and architecture search | Expert | Experimental: no equivalence guarantee |
| Board control, transceivers, timing distribution, online control | — | Unsupported: the board framework's |
| Package or dependency management, general CI | — | Unsupported: Hog, ipbb, FuseSoC |
| Conversion of arbitrary CMSSW collections to reference words | — | Unsupported: the subsystem's packer or codec |
| Timing closure or metastability from declarations | — | Unsupported: import the implementation tools' results |

## Enforcement Rule

A capability whose evidence no longer matches its row is reclassified,
fixed or removed in the same change.
