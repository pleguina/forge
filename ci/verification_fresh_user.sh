#!/usr/bin/env bash
# =============================================================================
# ci/verification_fresh_user.sh — a design verified from its input and
# expected output alone.
#
# Copies the foreign simple_pipeline fixture (plain RTL, no FORGE files) to a
# scratch directory, adopts it, and runs `forge test --input --expected` on
# Verilator. The only files added are the two data files; no Python, SV or
# YAML of verification is written. Fails unless the design level is VERIFIED,
# a corrupted expected word makes it FAILED, and its evidence names the data
# by hash.
#
# Usage: bash ci/verification_fresh_user.sh [scratch dir]
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${1:-$(mktemp -d)}"
mkdir -p "${WORK}"
PROJECT="${WORK}/simple_pipeline"
rm -rf "${PROJECT}"
cp -r "${REPO_ROOT}/forge/tests/fixtures/foreign/simple_pipeline" "${PROJECT}"
cd "${PROJECT}"

forge adopt . > /dev/null

# The data a user brings: 40 samples, and what the pipeline must make of them
# (every second sample, a two-tap average, a sequence number).
python3 - <<'PY'
import json
xs = [(7919 * i + 13) & 0xFFFF for i in range(40)]
out, prev = [], 0
for seq, d in enumerate(xs[1::2]):
    out.append((seq << 16) | (((d >> 1) + (prev >> 1)) & 0xFFFF))
    prev = d
json.dump({"decimator_sample_in": xs}, open("input.json", "w"))
json.dump({"packer_packet_out": out}, open("expected.json", "w"))
out[7] ^= 1
json.dump({"packer_packet_out": out}, open("wrong.json", "w"))
PY

forge test --plan --level design
forge test --input input.json --expected expected.json --backend verilator --level design --json > result.json
python3 - <<'PY'
import json
r = json.load(open("result.json"))
assert r["metrics"]["levels"] == {"design[verilator]": "VERIFIED"}, r["metrics"]
e = json.load(open(".forge/reports/test/auto/design.verilator.evidence.json"))
assert e["status"] == "VERIFIED" and e["expected"]["sha256"] and e["compile_key"], e
print("design VERIFIED, evidence", e["evidence_id"][:16])
PY

if forge test --input input.json --expected wrong.json --backend verilator --level design --json > wrong_result.json; then
    echo "FAIL: a wrong expected word passed" >&2
    exit 1
fi
python3 - <<'PY'
import json
e = json.load(open(".forge/reports/test/auto/design.verilator.evidence.json"))
rows = e["interfaces"][0]["rows"]
assert e["status"] == "FAILED" and [r["kind"] for r in rows] == ["mismatch"] and rows[0]["index"] == 7, rows
assert e["compile_reused"] is True, "new data recompiled the testbench"
print("wrong expected word FAILED at index 7, build reused")
PY
