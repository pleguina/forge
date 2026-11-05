# A payload compared with a CMSSW export

A real CMSSW sample, a small payload, and the comparison between them, laid out so that another
algorithm replaces three things and keeps the rest.

```bash
python3 make_patterns.py     # the export -> data/input.txt, data/expected.txt and their event names
forge test                   # Verilator and XSim, whichever is installed
forge report                 # .forge/reports/verification_results.md
```

## What is here

| File | What | For another algorithm |
|---|---|---|
| `export/omtf_muons.json` | 100 events of CMSSW's OMTF emulator: the track-finder candidates (CandMuon) and the GMT muons it made of them (SAMuonConstr), per processor, with the CMSSW release, commit and configuration it came from | your subsystem's collections, in the same shape |
| `export/from_omtf_xml.py` | how that export was written from the emulator's XML dump | the code that writes your export |
| `codecs/candidate.yml`, `codecs/samuon.yml` | where each field sits in an input and an output link word | your link word layouts |
| `rtl/candidate_to_gmt.v` | the payload: each link's candidates rewritten as GMT words | your algorithm |
| `make_patterns.py` | the export packed with the codecs into EMP pattern files, one packet per link and event | the collections and links to use |
| `forge.yml` | the top, the links each interface is on, which codec reads the outputs, how they are compared | your top, links and fields |

The export shape is all FORGE assumes about the reference:

```json
{"provenance": {"cmssw_release": "...", "cmssw_commit": "...", "config": {...}},
 "events": [{"id": 2, "candidates": [{"link": 0, "pt": 214, "eta": 1246, "phi": 2045, "charge": 0, "qual": 12}],
                      "samuons": [...]}]}
```

In this sample the emulator's candidates and its GMT muons carry the same values, so the payload is
the change of word format between the two link protocols. It stands for the algorithm stage you put
in its place: the comparison does not depend on what it computes.

## What a comparison reports

The outputs are compared by the fields `codecs/samuon.yml` names (`compare.fields`), in any order
within an event (`compare.order: event`); empty slots are not objects. Changing the payload so eta
comes out one unit high gives, in `forge report`:

```
| q0 | mismatch | 2 | 0 | 4 | 0x7000c004de3fe8359 | 0x7000c004df3fe8359 | expected: valid=1, charge=0, pt=214, phi=2045, eta=1246, d0=0, qual=12; observed: valid=1, charge=0, pt=214, phi=2045, eta=1247, d0=0, qual=12 |
```

with the CMSSW release and commit of the reference under it. `forge test --event-id 2` runs that
event alone. A packet missing on one link is an error naming the event and link, not a shift of
every later event:

```
event 3 (frames 6-6): link(s) 2 have no packet starting there, link(s) 0, 1, 3, 4, 5 do. Pairing
packets by position would shift every later event; set verification.association.missing: empty if
a link may carry no packet
```

## Where the sample came from

`cmsRun` of the OMTF Phase-2 emulator (L1TMuonOverlapPhase2, `dumpResultToXML`) in CMSSW_20_0_0
with github.com/pleguina/cmssw at 0338fcce45b, on single muons (flat pT 2 to 100 GeV, Phase-2
geometry D110), then `export/from_omtf_xml.py --events 100`. `export/omtf_muons.json` records the
same in its `provenance`, and the report shows it.

See [Compare a payload with a CMSSW export](../../docs/how-to/compare-with-a-cmssw-export.md) for a
full-chip payload done the same way.
