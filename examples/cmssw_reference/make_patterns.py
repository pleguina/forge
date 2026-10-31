"""The export -> the pattern files `forge test` plays and expects.

    python3 make_patterns.py

Reads export/omtf_muons.json and writes data/input.txt (the candidates, packed with
codecs/candidate.yml) and data/expected.txt (CMSSW's GMT muons, packed with codecs/samuon.yml),
one packet per link and event, and the event names next to them. For another algorithm, change
the collections, the codecs and the links; nothing here knows what a muon is.
"""
import json
from pathlib import Path

from forge.integration.objects import Codec, write_patterns

HERE = Path(__file__).resolve().parent
LINKS = range(6)


def main():
    export = json.loads((HERE / "export/omtf_muons.json").read_text())
    events = export["events"]
    data = HERE / "data"
    data.mkdir(exist_ok=True)
    for side, collection, codec in (("input", "candidates", "candidate"), ("expected", "samuons", "samuon")):
        n, objects = write_patterns(data / f"{side}.txt", events, objects=lambda e: e[collection],
                                    codec=Codec.load(HERE / f"codecs/{codec}.yml"), link_of=lambda o: o["link"],
                                    links=LINKS, names=data / f"{side}.events.json",
                                    name_of=lambda e: {"event": e["id"]})
        print(f"data/{side}.txt: {n} events, {objects} {collection}")
    (data / "provenance.json").write_text(json.dumps(export["provenance"], indent=1) + "\n")


if __name__ == "__main__":
    main()
