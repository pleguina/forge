"""Cross-command consistency check: every
top-level `forge` command's `--json` output must round-trip through
`CommandEnvelope.from_dict` and share the same `schema_version` — proof
that the envelope sweep actually landed everywhere it claims to, not just
in the commands with their own dedicated shape-migration tests.

Each command below is invoked with the cheapest real input that exercises
its JSON path (no xsim/real simulation needed — those are already
covered end-to-end by `test_test_cli_group.py`/`test_report_cli_group.py`/
`test_init_cli_group.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.core.cli.envelope import ENVELOPE_SCHEMA_VERSION, CommandEnvelope
from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_YML = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
MODULES_YML = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
DESIGN_VERIFICATION_YML = REPO_ROOT / "plugins/passthrough_demo/forge/verify/design.verification.yml"


def _run(capsys: pytest.CaptureFixture[str], argv: list) -> tuple:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(argv)
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out


_COMMANDS = [
    ["doctor", "--json"],
    ["inspect", str(DESIGN_YML), "--contracts-from", str(MODULES_YML), "--json"],
    ["build", str(DESIGN_YML), "--contracts-from", str(MODULES_YML), "--json"],
    ["topgen", "validate", str(DESIGN_YML), "--json"],
    ["topgen", "validate-registry", str(MODULES_YML), "--json"],
    ["core", "resources", "--json"],
    ["core", "verify-contract", "--ip-info", "does-not-exist.yaml", "--contract", "also-missing.yaml", "--json"],
    ["analyze", "latency-check", str(DESIGN_YML), "--json"],
    ["verify", "doctor", str(DESIGN_VERIFICATION_YML), "--json"],
]


@pytest.mark.parametrize("argv", _COMMANDS, ids=lambda argv: " ".join(argv[:2]))
def test_json_output_round_trips_through_command_envelope(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, argv: list,
) -> None:
    # analyze latency-check needs a real --output path.
    if argv[:2] == ["analyze", "latency-check"]:
        argv = argv + ["--output", str(tmp_path / "latency_check.md")]

    _code, out = _run(capsys, argv)
    payload = json.loads(out)

    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    envelope = CommandEnvelope.from_dict(payload)
    assert envelope.status in ("pass", "warn", "fail", "error")
    # Round-trips losslessly back to the same dict.
    assert envelope.to_dict() == payload
