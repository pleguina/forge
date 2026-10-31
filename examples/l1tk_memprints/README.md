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

## A firmware module against the emulation

The HLS modules of the chain read and write memories (`ap_memory` ports),
which FORGE's `memory` protocol drives. `vmr_l1phia/` checks the VMRouter of
layer 1, phi region A, built with Vitis HLS from the firmware repository,
against the emulation's memory printouts: its `vmr_data.py` (34 lines) feeds
the module as the firmware's own test bench does (input memories in
file-name order, each event in page `bx % 4` with its entry count, `bx` =
event number) and names the outputs compared. Its `forge.yml` expects a firmware-hls
checkout (or a link to one) named `firmware-hls` next to it, after
`emData/download.sh` and the module's HLS build, then:

```bash
forge test
```

On the 100 events of the September 2025 test vectors, all 938 AllStub words
and all 938 ME stubs match, each in its own event. Vitis HLS 2024.1 builds
the module with a read latency of 1 on its input memories: it drops the
firmware's `#pragma HLS resource ... latency=2` as deprecated, and a
declared latency of 2 fails with every word one place late.
