"""
End-to-end coverage for the `forge inspect` CLI command — the first
consumer of the canonical IR (forge.ir). Drives the real argparse entry
point in-process against plugins/passthrough_demo, mirroring the pattern
used in test_topgen_cli_commands.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_YML = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
MODULES_YML = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN_YML = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES_YML = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run_inspect(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["inspect", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


def _tree_snapshot(root: Path) -> frozenset[str]:
    if not root.exists():
        return frozenset()
    return frozenset(str(p.relative_to(root)) for p in root.rglob("*"))


def test_inspect_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_inspect(capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "forge inspect" in result.stdout
    assert "content hash" in result.stdout
    assert "modules           : 1" in result.stdout


def test_inspect_json_output_is_well_formed(capsys: pytest.CaptureFixture[str]) -> None:
    """Plain `--json` now emits one
    CommandEnvelope summary (status/diagnostics/metrics/next_actions), not
    a full IR dump — the full canonical IR is still obtainable via
    `--emit-ir` (see test_inspect_emit_ir_writes_exactly_the_requested_file
    below), which is unchanged."""
    result = _run_inspect(capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    # passthrough_demo is single-module and declares no reference period, so
    # neither the unwired-design nor the clock-divisibility check applies —
    # a reference plugin inspects clean. (Both used to fire here as false
    # positives; see test_topgen_cli_commands.py's
    # test_reference_design_validates_without_warnings.)
    assert payload["status"] == "pass"
    assert payload["diagnostics"] == []
    assert payload["schema_version"]
    assert payload["metrics"]["content_hash"]
    assert payload["metrics"]["counts"]["modules"] == 1
    assert payload["metrics"]["maturity"]["modules"]["total"] == 1
    # forge inspect never runs the generator — the port-accounting fields
    # stay honestly absent rather than fabricated.
    assert payload["metrics"]["maturity"]["ports"] is None
    assert payload["metrics"]["maturity"]["strict_pass"] is None


def test_inspect_never_writes_without_emit_ir(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """forge inspect is read-only by default — the same invariant as
    `topgen gen-top --dry-run`, verified the same way (tree snapshot)."""
    tree_before = _tree_snapshot(tmp_path)

    import os
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = _run_inspect(capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML))
    finally:
        os.chdir(old_cwd)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _tree_snapshot(tmp_path) == tree_before


def test_inspect_emit_ir_writes_exactly_the_requested_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    out_path = tmp_path / "nested" / "design.ir.json"

    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--emit-ir", str(out_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["schema_version"] == "0.3.0"  # module compile set + declaration order
    # Nothing else was written.
    assert _tree_snapshot(tmp_path) == frozenset({"nested", "nested/design.ir.json"})


def test_inspect_diff_against_itself_reports_no_changes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    ir_path = tmp_path / "design.ir.json"
    _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--emit-ir", str(ir_path),
    )

    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--diff", str(ir_path), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    # --diff's result is a query result, not a diagnostic, so
    # it moves into metrics["diff"] rather than being top-level keys.
    diff = payload["metrics"]["diff"]
    assert diff["hash_equal"] is True
    assert diff["instances"] == {"added": [], "removed": [], "changed": []}


def test_inspect_diff_round_trips_matching_evidence(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Release-plan §3.5 (Slice 5): a connection carrying matching_evidence
    (trigger_demo's gather-pattern/wiring_kind/protocol/width-bearing
    connections) must survive --emit-ir -> --diff losslessly. Regression
    guard for the round-trip-drops-a-field bug class hit and fixed three
    times before this field existed (emission_order, top_ports, protocol)."""
    ir_path = tmp_path / "design.ir.json"
    _run_inspect(
        capsys, str(TRIGGER_DESIGN_YML), "--contracts-from", str(TRIGGER_MODULES_YML),
        "--emit-ir", str(ir_path),
    )
    emitted = json.loads(ir_path.read_text())
    conns_with_evidence = [
        c for c in emitted["design"]["connections"] if c.get("matching_evidence")
    ]
    assert conns_with_evidence  # the emitted IR actually has some to round-trip

    result = _run_inspect(
        capsys, str(TRIGGER_DESIGN_YML), "--contracts-from", str(TRIGGER_MODULES_YML),
        "--diff", str(ir_path), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    diff = payload["metrics"]["diff"]
    assert diff["hash_equal"] is True
    assert diff["connections"] == {"added": [], "removed": [], "changed": []}


def test_inspect_provenance_writes_manifest(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    prov_path = tmp_path / "provenance.json"

    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--provenance", str(prov_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert prov_path.exists()
    payload = json.loads(prov_path.read_text())
    assert payload["ir_content_hash"]
    # source_hashes keys are relative to design.yml's own
    # directory, not absolute paths — the design file itself keys as
    # its own bare name.
    assert "design.yml" in payload["source_hashes"]


def test_inspect_explain_staleness_fresh_then_stale(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    prov_path = tmp_path / "provenance.json"
    _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--provenance", str(prov_path),
    )

    fresh = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--explain-staleness", str(prov_path), "--json",
    )
    assert fresh.returncode == 0
    fresh_payload = json.loads(fresh.stdout)
    # --explain-staleness's result is a query result, not a
    # diagnostic, so it moves into metrics["explain_staleness"].
    fresh_staleness = fresh_payload["metrics"]["explain_staleness"]
    assert fresh_staleness["stale"] is False
    assert fresh_staleness["reasons"] == []

    # Different --build-dir counts as a changed command option, even though
    # it has no effect here (contracts_from already resolves everything) —
    # this exercises the "command options changed" staleness reason.
    stale = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--build-dir", str(tmp_path / "some_build_dir"),
        "--explain-staleness", str(prov_path), "--json",
    )
    assert stale.returncode == 1
    stale_payload = json.loads(stale.stdout)
    stale_staleness = stale_payload["metrics"]["explain_staleness"]
    assert stale_staleness["stale"] is True
    assert any("command options changed" in r for r in stale_staleness["reasons"])


def test_inspect_explain_staleness_missing_file_is_guided(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--explain-staleness", str(tmp_path / "does-not-exist.json"),
    )

    assert result.returncode == 1
    assert "not found" in result.stdout


def test_inspect_missing_design_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.yml"
    result = _run_inspect(capsys, str(missing))

    assert result.returncode == 1
    assert "Design file not found" in result.stdout
    assert "Traceback" not in result.stderr


def test_inspect_missing_diff_file_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--diff", str(tmp_path / "does-not-exist.json"),
    )

    assert result.returncode == 1
    assert "not found" in result.stdout


def _write_one_contract_one_compat_design(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A synthetic 2-module design where `src` has a real interface
    contract (contract-driven) and `dst` has none (falls back to
    heuristic clk/rst matching — compat mode) — the compat-mode-module
    case, needed for
    `test_inspect_next_actions_flags_compat_mode_modules` below since
    neither real reference plugin (passthrough_demo, trigger_demo) has a
    compat-mode module today (confirmed: both report
    `maturity["modules"]["compat_mode"] == 0`).

    A pre-built `--ip-info` is required alongside `--contracts-from`: when
    contracts are given but don't cover every module, both
    `forge.ir.build._resolve_ip_info` and `compute_gen_top_plan`'s own
    ip_info resolution only synthesize ip_info for the *contract-covered*
    subset (no build artifacts exist here to fall back to for `dst`) — so
    without an explicit `--ip-info`, `dst`'s ports never resolve at all
    and the whole design fails to build, never reaching compat mode.
    """
    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n"
        "  ip_info_key: src\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst, direction: input, width: 1}\n"
        "    dout: {raw_port: dout, direction: output, width: 8}\n"
    )
    modules_yml = tmp_path / "modules.yml"
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n"
        "    kind: rtl\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
        "  - name: dst\n"
        "    kind: rtl\n"
        "    top: dst_top\n"
        "    src: [dst.v]\n"
    )
    ip_info_yml = tmp_path / "ip_info.yaml"
    ip_info_yml.write_text(
        "src:\n"
        "  vendor: test\n"
        "  library: test\n"
        "  name: src\n"
        "  entity: src_top\n"
        "  version: '1.0'\n"
        "  kind: rtl\n"
        "  ports:\n"
        "    - {name: clk, direction: INPUT, width: 1, type: std_logic}\n"
        "    - {name: rst, direction: INPUT, width: 1, type: std_logic}\n"
        "    - {name: dout, direction: OUTPUT, width: 8, type: std_logic_vector}\n"
        "dst:\n"
        "  vendor: test\n"
        "  library: test\n"
        "  name: dst\n"
        "  entity: dst_top\n"
        "  version: '1.0'\n"
        "  kind: rtl\n"
        "  ports:\n"
        "    - {name: clk, direction: INPUT, width: 1, type: std_logic}\n"
        "    - {name: rst, direction: INPUT, width: 1, type: std_logic}\n"
        "    - {name: din, direction: INPUT, width: 8, type: std_logic_vector}\n"
    )
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: src\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "  - name: dst\n"
        "    top: dst_top\n"
        "    src: [dst.v]\n"
        "connections:\n"
        "  - from: src\n"
        "    to: dst\n"
        "    port_map: [[dout, din]]\n"
    )
    return design_yml, modules_yml, ip_info_yml


def test_inspect_maturity_summary_present_on_real_designs(capsys: pytest.CaptureFixture[str]) -> None:
    """Real-design (not synthetic) coverage for the maturity summary's
    presence and honest port-accounting absence on both reference
    plugins."""
    for design, modules in (
        (DESIGN_YML, MODULES_YML),
        (TRIGGER_DESIGN_YML, TRIGGER_MODULES_YML),
    ):
        result = _run_inspect(capsys, str(design), "--contracts-from", str(modules), "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        maturity = json.loads(result.stdout)["metrics"]["maturity"]
        assert maturity["modules"]["total"] > 0
        assert maturity["ports"] is None
        assert maturity["strict_pass"] is None


def test_inspect_next_actions_flags_compat_mode_modules(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    design_yml, modules_yml, ip_info_yml = _write_one_contract_one_compat_design(tmp_path)

    result = _run_inspect(
        capsys, str(design_yml),
        "--contracts-from", str(modules_yml),
        "--ip-info", str(ip_info_yml),
        "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["metrics"]["maturity"]["modules"]["compat_mode"] == 1
    assert (
        "Add interface contracts or pass --strict at generation time"
        in payload["next_actions"]
    )


def test_inspect_next_actions_flags_errors(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """A design.yml with two modules sharing the same name — a genuine
    validate_design ERROR diagnostic ("Duplicate module name") that
    survives all the way into the IR's own diagnostics list (unlike a
    missing `part`, which crashes `DesignConfig` construction itself
    before any diagnostic is ever produced) — surfaces "Fix the errors
    above" in next_actions, and the envelope status is "fail"."""
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: src\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "  - name: src\n"
        "    top: src_top2\n"
        "    src: [src2.v]\n"
    )

    result = _run_inspect(capsys, str(design_yml), "--json")

    payload = json.loads(result.stdout)
    assert payload["status"] == "fail"
    assert result.returncode == 1
    assert any(
        "Duplicate module name" in d["message"] and d["severity"] == "error"
        for d in payload["diagnostics"]
    )
    assert "Fix the errors above before generating" in payload["next_actions"]


def test_inspect_explorer_overlays_absent_without_the_new_flags(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Regression guard: --explorer
    without --verify-design/--results-json must be unaffected."""
    explorer_path = tmp_path / "explorer.html"
    result = _run_inspect(
        capsys, str(TRIGGER_DESIGN_YML), "--contracts-from", str(TRIGGER_MODULES_YML),
        "--explorer", str(explorer_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    explorer_html = explorer_path.read_text()
    assert '"verification_flow_entry_points":["hit_decoder_xsim"]' not in explorer_html


def test_inspect_explorer_carries_real_latency_and_verification_overlays(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """forge inspect --verify-design
    --results-json --explorer/--dot must carry real latency values
    (trigger_logic's real declared `latency: {kind: fixed, cycles: 3}`)
    and a real verification-flow-entry-point join (hit_decoder_xsim's
    declared entry point, module 'dec') — not just exit 0. results.json
    is a minimal, directly-constructed {flow_name, backend_id} payload,
    the same convention test_design_explorer_overlays.py's own
    join_flow_entry_points tests already use."""
    results_json = tmp_path / "results.json"
    results_json.write_text(json.dumps({"flow_name": "hit_decoder_xsim", "backend_id": "xsim"}))
    verify_design = TRIGGER_DESIGN_YML.parent.parent / "verify/design.verification.yml"
    dot_path = tmp_path / "topo.dot"
    explorer_path = tmp_path / "explorer.html"

    result = _run_inspect(
        capsys, str(TRIGGER_DESIGN_YML), "--contracts-from", str(TRIGGER_MODULES_YML),
        "--verify-design", str(verify_design), "--results-json", str(results_json),
        "--dot", str(dot_path), "--explorer", str(explorer_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    dot_text = dot_path.read_text()
    assert 'label="trig clk: ap_clk maturity: mixed latency: 3c"' in dot_text

    explorer_html = explorer_path.read_text()
    assert '"verification_flow_entry_points":["hit_decoder_xsim"]' in explorer_html
