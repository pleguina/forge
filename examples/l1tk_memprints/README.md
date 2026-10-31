# L1 track finder memory printouts

The [L1 track finder firmware](https://github.com/cms-L1TK/firmware-hls) is
tested against the contents of every memory between its processing steps,
written by the CMSSW L1 track emulation (`emData/download.sh` fetches them;
the format is described in its README). FORGE reads them through
`verification.formats` with `memprint.py`, with no change to FORGE:

```yaml
verification:
  target: existing
  formats: {l1tk-memprint: memprint.py:load}
  compare: {order: event}
  input:    {path: MemPrints/InputStubs, format: l1tk-memprint}
  expected: {path: MemPrints/Stubs, format: l1tk-memprint}
```

A data path is a directory of printouts, one file per memory; each event
names a memory by its interface name (`NAMES` in `memprint.py`, else the
memory name in lower case) with its words in the order the emulation wrote
them, and an event with no entries stays an empty one. A change to any file
of the directory, or to `memprint.py`, makes the last result out of date.

The memory's address (and a binned memory's bin) is not kept: `order:
event` compares each event's words in any order.

## Example: one firmware module against the emulation

`vmr_l1phia/` checks one HLS module of the L1 track finder, the VMRouter of
layer 1, phi region A, against the emulation's memory printouts. It is an
example to copy, not a test of the whole chain. The module reads and writes
memories (`ap_memory` ports), which FORGE's `memory` protocol drives; the
only project code is `vmr_data.py` (34 lines), which feeds the module as the
firmware's own test bench does (input memories in file-name order, each
event in page `bx % 4` with its entry count, `bx` = event number) and names
the outputs compared.

Needs Vivado/Vitis HLS (2024.1 here) and network access for the test
vectors. From `vmr_l1phia/`:

```bash
git clone https://github.com/cms-L1TK/firmware-hls.git
git -C firmware-hls checkout 898d6e28f78fba7c1eb97380223993a98e5f105b
(cd firmware-hls/emData && ./download.sh)            # test vectors, LUTs, generated top functions

# build the one module (about 30 s)
(cd firmware-hls/IntegrationTests/ReducedCombinedConfig_FPGA1/script &&
 vitis_hls -f ../../common/script/compileHLS.tcl \
   ../../../TopFunctions/ReducedCombinedConfig/VMRouterCMTop_L1PHIA.cc VMRouterCMTop_L1PHIA)

forge test
```

```
  ✅ existing:VMRouterCMTop_L1PHIA [xsim]: VERIFIED (memoryME 938/938, memoriesAS_dataarray_s 938/938)
```

All 938 AllStub words and all 938 ME stubs of the 100 events match, each in
its own event; `forge test --event-id 42 --pre-roll 2` replays one event.

For another module of the chain, copy `vmr_l1phia/`, change `top` and
`sources.rtl`, and rewrite `vmr_data.py` for its memories (its C++ test
bench under `TestBenches/` says which printouts feed which port).

Vitis HLS 2024.1 builds this module with a read latency of 1 on its input
memories: it drops the firmware's `#pragma HLS resource ... latency=2` as
deprecated. A declared latency of 2 fails with every word one place late,
the first one X, so the declaration in `forge.yml` must match what HLS built.
