#!/usr/bin/env python3
# ci/check_coverage_floors.py — Per-subsystem coverage floors for the
# modules that back FORGE's published correctness claims (canonical IR
# assembly, contract matching/parsing, structural generation, verification
# backends).
#
# The single global `--cov-fail-under` in forge/pyproject.toml only catches
# an overall regression; it can't catch one high-stakes subsystem quietly
# eroding while well-tested CLI/docs code pulls the average back up. This
# script holds specific subsystems to their own floor instead.
#
# Floors below are set at (or just above) the measured baseline as of
# 2026-09-17 (`pytest forge/tests -m "not integration"`), NOT at each
# group's stated target — several groups aren't at target yet, and this
# script says so rather than silently gating on an aspirational number
# that would fail today. Raise a floor only once the measured number
# actually clears it, same ratchet discipline as the global floor's
# history in CHANGELOG.md ("coverage floor" entries).
#
# Usage: python3 ci/check_coverage_floors.py [coverage.json]
# (generate the input with `coverage json -o coverage.json` right after the
# pytest run that produced .coverage — see forge:python-unit-tests in
# .gitlab-ci.yml)

import json
import sys

# (label, path_prefix, excluded_path_prefixes, floor_pct, target_pct, note)
GROUPS = [
    (
        "forge/ir/*",
        "forge/ir/",
        (),
        90,
        90,
        "Canonical IR assembly/serialization/provenance — already at target.",
    ),
    (
        "forge/contracts/* (core matching/parsing)",
        "forge/contracts/",
        ("forge/contracts/unpacker.py",),
        80,
        90,
        "forge/contracts/unpacker.py (IP archive unpacking) is excluded — "
        "secondary tooling, not core matching/parsing. matcher.py and "
        "parser.py are the two files keeping this group below target.",
    ),
    (
        "forge/generation/generators/*",
        "forge/generation/generators/",
        (),
        70,
        90,
        "sv_testbench_generator.py was 5% covered as of 2026-09-17 (the "
        "file the most recent shipped bugfix, 35b7c6e, touched) and is "
        "now at 100% — raised this group's floor 50% -> 70% to match. "
        "structural_vhdl.py (55%) and design_parameters.py (54%) are the "
        "two files keeping this group below target now.",
    ),
    (
        "forge/verification/backend_*",
        "forge/verification/backend_",
        (),
        55,
        90,
        "backend_csim.py and backend_verilator.py measure low under "
        "`pytest -m 'not integration'`; Vitis HLS and Verilator are both "
        "actually installed in CI, so re-measure with integration tests "
        "included before treating this as a real gap rather than a "
        "marker-exclusion artifact.",
    ),
]


def main(coverage_json_path: str) -> int:
    with open(coverage_json_path) as f:
        data = json.load(f)

    files = data["files"]
    failed = False

    for label, prefix, excludes, floor, target, note in GROUPS:
        stmts = 0
        covered = 0
        matched = 0
        for path, info in files.items():
            if not path.startswith(prefix):
                continue
            if any(path.startswith(ex) for ex in excludes):
                continue
            matched += 1
            summary = info["summary"]
            stmts += summary["num_statements"]
            covered += summary["covered_lines"]

        if matched == 0:
            print(f"WARN  {label}: no files matched prefix {prefix!r} — check the path")
            continue

        pct = 100.0 * covered / stmts if stmts else 100.0
        ok = pct >= floor
        failed = failed or not ok
        print(
            f"{'OK  ' if ok else 'FAIL'}  {label}: {pct:.1f}% "
            f"(floor {floor}%, target {target}%) — {covered}/{stmts} lines, "
            f"{matched} files"
        )
        print(f"        {note}")

    print()
    if failed:
        print("One or more subsystem coverage floors were not met.")
        return 1

    print("All subsystem coverage floors met.")
    return 0


if __name__ == "__main__":
    coverage_json = sys.argv[1] if len(sys.argv) > 1 else "coverage.json"
    sys.exit(main(coverage_json))
