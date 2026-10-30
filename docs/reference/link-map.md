# Link map and build identity

`forge dev framework link-map` writes one JSON that answers, for a built
payload, which fibre carries what into which algorithm port, how its
registers are laid out, and which build all of that describes. It is meant
for online software (SWATCH, HERD) and commissioning.

```bash
forge dev framework link-map --detector-io detector_io.resolved.json \
  --file ir=gen/design.ir.json --file payload=gen/payload.v \
  --file bitstream=top.bit --file dataset=patterns/source.txt \
  --repo . --registers registers.yml --clock logic_clk \
  --out link_map.json
```

| Key | Content |
|-----|---------|
| `links` | Every resolved link: endpoint, direction, SLR, quad, lane, lane width, protocol, cage, fibre, polarity, logical id and name, role; the algorithm port and front-end instance of an input, the source port of an output |
| `link_clock` | The clock the link words are handed over in (`--clock`) |
| `registers` | With `--registers`: the [register map](registers.md)'s word layout, access, reset values and fields |
| `build.files` | sha256 of each `--file` and of the resolved I/O |
| `build.source` | With `--repo`: its commit and whether tracked files differ from it |
| `build.build_id` | sha256 over FORGE's version, the file hashes and the commit |
| `build.build_id32` | Its first 32 bits, small enough to drive a status register so the firmware reports which build it is |

The same files give the same `build_id`; changing any of them, or building
from another commit or a modified checkout, gives another.
