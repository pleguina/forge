"""The export this example compares with, written from the OMTF emulator's XML dump.

    python3 from_omtf_xml.py TestEvents_*.xml --events 100 --provenance provenance.json > omtf_muons.json

CMSSW's L1TMuonOverlapPhase2 emulator dumps, per event and processor, the candidates it found
(CandMuon) and the GMT muons it made of them (SAMuonConstr). This keeps the two collections,
their fields named as the codecs name them, and the link each processor's objects travel on:
processors 0-2 of the positive side on links 0-2, of the negative side on links 3-5.

This file is the subsystem-specific part: another algorithm writes its own collections in the
same shape, {"provenance": {...}, "events": [{"id": ..., "<collection>": [{field: value, "link": n}]}]}.
"""
import argparse
import json
import xml.etree.ElementTree as ET


def muon(m, link):
    return {"link": link, "pt": int(m.attrib["hwPt"]), "eta": int(m.attrib["hwEta"]),
            "phi": int(m.attrib["hwPhi"]), "charge": int(m.attrib["hwSign"]), "qual": int(m.attrib["hwQual"])}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xml")
    ap.add_argument("--events", type=int, default=100)
    ap.add_argument("--provenance", help="JSON with the CMSSW release, commit and configuration")
    args = ap.parse_args()
    events = []
    for ev in ET.parse(args.xml).getroot().findall("Event")[:args.events]:
        candidates, samuons = [], []
        for pr in ev.iter("Processor"):
            link = int(pr.attrib["iProcessor"]) + (3 if pr.attrib["position"].startswith("-") else 0)
            candidates += [muon(m, link) for m in pr.findall("CandMuon")]
            samuons += [muon(m, link) for m in pr.findall("SAMuonConstr")]
        events.append({"id": int(ev.attrib["iEvent"]), "candidates": candidates, "samuons": samuons})
    provenance = json.load(open(args.provenance)) if args.provenance else {}
    print(json.dumps({"provenance": provenance, "events": events}, indent=1))


if __name__ == "__main__":
    main()
