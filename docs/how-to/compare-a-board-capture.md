# Compare a board capture

The reference a payload was simulated against is what its board build
should produce too. FORGE packages that reference for the board, and
compares what the board captured with the scoreboard simulation uses, so a
project keeps no separate hardware checker.

FORGE neither loads nor captures: EMP, Blobfish or the board's own tools
play the input and capture the output links. The reference must be pattern
files (`empv2`, `apx`) with `verification.links` mapping each interface to
its link.

## Package the reference

```bash
forge report --package payload
```

`.forge/reports/packages/payload/` then holds:

| File | What |
|---|---|
| `data/input.*`, `data/expected.*` | the target's input and expected output, and their `events:` names |
| `manifest.json` | the link of every interface, the association and comparison settings, the checker version, the plan identity, the decoder or codecs and the memory files, whether the reference is independent or derived (recorded traffic), the build identity (top, every source and header by hash, defines, for a generated top the IR and output hashes of its build, and the firmware identity a board should read back), and the simulation results of the same reference |
| `README.md` | the same, for the person at the board |

`--package-events 7 --pre-roll 4` makes it the package of one failing
boundary: the manifest and README carry the command that reproduces it in
another checkout (`forge test --target payload --event-list 7 --pre-roll 4`).
`--external-data` names reference files too large to copy by path and
checksum instead. `--bitfile top.bit` records the bitstream built from
these sources by checksum.

## Compare the capture

```bash
forge test --target payload --capture capture_tx.txt --package .forge/reports/packages/payload
```

```
  ❌ payload [board]: FAILED (q 1/2), build 006133c3d013
      q mismatch event=355100:1:42@bx9 index=1 cycle=7 expected=0x70000000000000021 observed=0x7000000000000002f
```

(An EMP link word is shown with its start, last and valid flags above the
64 data bits, as in simulation.)

Each compared output is read from its link in the capture (a stream from
its members' links when every member is mapped, else from its own), its
valid words in frame order, and compared by the same function as a
simulation's output: the same compared outputs, decoder fields, empty-word
policy, event attribution and compare settings, where a capture's cycles
are its frames. Mismatch
rows carry the event name, the decoder's fields (`verification.decoder`) and
the frame. The result is recorded as the target's `board` result, with the
capture, the links read and the build it came from; `forge report` shows it
beside the simulations, and it goes out of date when the capture, the
reference or the sources change.

With `--package`, the comparison is refused when the input, expected output
or links in `forge.yml` are no longer the package's: the capture is compared
with the reference that was played. Without it, the current reference and
build are recorded.

## Which firmware the capture came from

The package's build identity is the firmware intended: its sources, the
project's git commit (`git_sha`, what Hog and most build flows write into a
register) and, with `--bitfile`, the bitstream's checksum. What the board
actually ran is only known from a readback: `--firmware-id readback.json`
takes the fields your board tools read (`{"git_sha": "...",
"bitfile_sha256": "...", "fw_id": "0x..."}`). The result records the
firmware as `confirmed` (every shared field agrees), `differs`,
`not_comparable` (no shared field) or `unconfirmed` (no readback); anything
but `confirmed` is a warning. FORGE reads no registers itself.

A latency mode measures from the input, so it needs `--capture-offset N`:
the capture frame at which the input's frame 0 was played. Only the board
setup knows it; FORGE does not assume one.
