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
event` compares each event's words in any order. A design whose outputs are
memories (address, write enable, data) rather than streams needs those
written out as streams by its test harness, as the firmware's own
`FileWriterFromRAM` does.
