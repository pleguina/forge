"""End-to-end coverage for the `forge topgen` CLI commands.

core/cli/groups/topgen.py is a ~900-line, essentially-0%-covered CLI
surface — everything in it was only exercised indirectly through
integration scripts. These tests drive the real argparse entry point
in-process (subprocess.run in test_topgen_cli_error_handling.py exercises
the CLI too, but a child interpreter's execution is invisible to
--cov=forge running in the parent pytest process, so it never counted
towards coverage) against plugins/passthrough_demo, a real, tiny,
checked-in FORGE consumer that needs no EDA tools (GHDL/Verilator/Vivado)
to generate structural Verilog.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_YML = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
MODULES_YML = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _tree_snapshot(root: Path) -> frozenset[str]:
    """Relative paths of every file/dir under root, for before/after dry-run diffs."""
    if not root.exists():
        return frozenset()
    return frozenset(str(p.relative_to(root)) for p in root.rglob("*"))


def _run_topgen(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["topgen", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


def test_validate_passes_on_real_design(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_topgen(capsys, "validate", str(DESIGN_YML))

    assert result.returncode == 0
    assert "Design is valid and ready for generation" in result.stdout


def test_validate_strict_fails_on_warnings(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_topgen(capsys, "validate", str(DESIGN_YML), "--strict")

    # passthrough_demo's design.yml has a non-fatal clock-period warning and
    # a "no connections" warning; --strict promotes those to failures.
    assert result.returncode == 1


def test_validate_json_output_is_well_formed(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_topgen(capsys, "validate", str(DESIGN_YML), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["registry"]["passed"] is True
    assert len(payload["design"]["warnings"]) == 2
    assert payload["design"]["errors"] == []


def test_validate_json_output_on_missing_design(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.yml"

    result = _run_topgen(capsys, "validate", str(missing), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert "Design file not found" in payload["error"]


def test_validate_registry_passes_on_real_registry(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_topgen(capsys, "validate-registry", str(MODULES_YML))

    assert result.returncode == 0
    assert "Registry validation passed" in result.stdout


def test_validate_registry_json_output_is_well_formed(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_topgen(capsys, "validate-registry", str(MODULES_YML), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload == {"passed": True, "errors": [], "warnings": [], "infos": []}


def test_ip_summary_writes_ip_info_yaml(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    output = tmp_path / "ip_info.yaml"

    result = _run_topgen(
        capsys, "ip-summary", str(DESIGN_YML),
        "--build-dir", str(tmp_path / "build"),
        "--output", str(output),
    )

    assert result.returncode == 0
    assert output.exists()
    assert "passthrough" in output.read_text()


def test_match_ports_reports_connections(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    ip_info = tmp_path / "ip_info.yaml"
    _run_topgen(
        capsys, "ip-summary", str(DESIGN_YML),
        "--build-dir", str(tmp_path / "build"), "--output", str(ip_info),
    )

    result = _run_topgen(capsys, "match-ports", str(DESIGN_YML), "--ip-info", str(ip_info))

    assert result.returncode == 0
    assert "Connections:" in result.stdout
    # covers the global_nets fan-out summary line too (ap_clk/ap_rst auto-mapped)
    assert "Global nets:" in result.stdout


def test_match_ports_missing_ip_info_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_topgen(
        capsys, "match-ports", str(DESIGN_YML),
        "--ip-info", str(tmp_path / "does-not-exist.yaml"),
    )

    assert result.returncode == 1
    assert "IP info file not found" in result.stdout
    assert "Traceback" not in result.stderr


def test_gen_top_verilog_full_pipeline(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """One real, tool-free gen-top run exercises the bulk of cmd_gen_top:
    validation, IP summary auto-generation, structural Verilog emission,
    build manifest, port map, design parameters, probe map, and TB bindings.
    """
    output = tmp_path / "algo_top.v"

    result = _run_topgen(
        capsys, "gen-top", str(DESIGN_YML),
        "--mode", "verilog",
        "--output", str(output),
        "--build-dir", str(tmp_path / "build"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.exists()
    assert "module algo_top" in output.read_text()
    for artifact in (
        "build_manifest.json",
        "port_map.yaml",
        "design_parameters.json",
        "probe_map.yaml",
        "tb_bindings.svh",
        "design.ir.json",
    ):
        assert (tmp_path / artifact).exists(), f"missing {artifact}"

    ir_payload = json.loads((tmp_path / "design.ir.json").read_text())
    assert ir_payload["schema_version"] == "0.2.0"  # release-plan §3.3 kind-vocabulary expansion
    assert len(ir_payload["design"]["modules"]) == 1

    # Migration step 6: design.ir.json's top_ports must be a real,
    # non-empty cross-check against port_map.yaml's port list from the
    # same run — same names, same count.
    top_port_names = {p["name"] for p in ir_payload["design"]["top_ports"]}
    assert top_port_names
    port_map = yaml.safe_load((tmp_path / "port_map.yaml").read_text())
    port_map_names = {
        entry["name"]
        for group in port_map["port_groups"].values()
        for entry in (group if isinstance(group, list) else group.get("ports", group.get("channels", [])))
        if isinstance(entry, dict) and "name" in entry
    }
    assert top_port_names == port_map_names


def test_gen_top_verilog_emits_tie_off_connection_for_an_open_input(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """release-plan §3.3: a tied-to-zero input must appear in the emitted
    design.ir.json as a real `tie_off` connection (`$tie_off` producer
    sentinel), not silently invisible to the IR."""
    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n"
        "  ip_info_key: src\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}\n"
        "    unconnected_in: {raw_port: unconnected_in, direction: input, width: 8}\n"
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
    )
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: src\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "connections: []\n"
    )

    result = _run_topgen(
        capsys, "gen-top", str(design_yml),
        "--mode", "verilog",
        "--output", str(tmp_path / "algo_top.v"),
        "--contracts-from", str(modules_yml),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    ir_payload = json.loads((tmp_path / "design.ir.json").read_text())
    tie_offs = [
        c for c in ir_payload["design"]["connections"]
        if c["producer"]["instance_id"] == "$tie_off"
    ]
    assert tie_offs == [{
        "id": "tie_off:src.unconnected_in",
        "producer": {"instance_id": "$tie_off", "interface_name": None, "port": None},
        "consumer": {"instance_id": "src", "interface_name": None, "port": "unconnected_in"},
        "wiring_method": None,
        "transformations": [{
            "id": "xform:tie_off:src.unconnected_in", "kind": "tie_off",
            "cycles": None, "tag": None,
        }],
        "emission_order": 0,
        "crosses_clock_domain": False,
        "crosses_reset_domain": False,
        # Slice 5 (release-plan §3.5): matching_evidence is only sourced
        # for conn_map-derived connections, not tie_off's synthetic
        # post-generation ones — stays None here, correctly.
        "matching_evidence": None,
    }]


def test_gen_top_design_ir_matches_fresh_inspect(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """Migration step 4 (docs/development/release-readiness.md): gen-top's
    design.ir.json (assembled from objects it already computed during
    generation) must be identical, content-hash-wise, to a fresh
    forge.ir.build.build_project_ir() call independently re-resolving the
    same design from scratch — proving the build_project_ir/assemble_project_ir
    split didn't silently diverge behavior between the two call paths.

    One documented exception (migration step 6): `top_ports` is populated
    from the generator's own report *after* generation runs, so gen-top's
    IR has it and a fresh, generation-free build_project_ir() call never
    can — compared separately below rather than folded into the hash
    comparison.
    """
    output = tmp_path / "algo_top.v"
    modules_yml_abs = str(MODULES_YML)

    result = _run_topgen(
        capsys, "gen-top", str(DESIGN_YML),
        "--mode", "verilog",
        "--output", str(output),
        "--build-dir", str(tmp_path / "build"),
        "--contracts-from", modules_yml_abs,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    from forge.ir.build import build_project_ir
    from forge.ir.serialize import content_hash

    ir_payload = json.loads((tmp_path / "design.ir.json").read_text())
    assert ir_payload["design"]["top_ports"]  # gen-top populated it
    gentop_design = dict(ir_payload["design"])
    gentop_design["top_ports"] = []  # strip before comparing (see docstring)
    gentop_hash = hashlib.sha256(
        json.dumps(
            {"schema_version": ir_payload["schema_version"], "design": gentop_design},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()

    fresh = build_project_ir(str(DESIGN_YML), contracts_from=modules_yml_abs)
    assert fresh.design.top_ports == []  # fresh (generation-free) build never has it
    assert gentop_hash == content_hash(fresh)


def test_gen_top_dry_run_lists_without_writing(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """--dry-run must never create, modify, or delete any project file —
    not even ip_info.yaml, which a real run auto-generates as a cached
    input. Regression test for the confirmed dry-run write violation
    (gen-top used to write ip_info.yaml to disk before checking dry_run).
    """
    output = tmp_path / "algo_top.v"

    tree_before = _tree_snapshot(tmp_path)

    result = _run_topgen(
        capsys, "gen-top", str(DESIGN_YML),
        "--mode", "verilog",
        "--output", str(output),
        "--build-dir", str(tmp_path / "build"),
        "--dry-run",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[dry-run] Would write:" in result.stdout
    assert str(output) in result.stdout
    assert str(tmp_path / "build_manifest.json") in result.stdout
    assert "[dry-run] No files written." in result.stdout

    # The whole point of --dry-run: the project tree is byte-for-byte
    # identical afterwards, including ip_info.yaml, the output directory,
    # and the build directory (none of which existed beforehand).
    assert _tree_snapshot(tmp_path) == tree_before
    assert not output.exists()
    assert not (tmp_path / "ip_info.yaml").exists()
    assert not (tmp_path / "build").exists()
    for artifact in (
        "build_manifest.json", "port_map.yaml", "port_signature.json",
        "design_parameters.json", "probe_map.yaml", "tb_bindings.svh",
        "maturity_report.json", "design.ir.json",
    ):
        assert not (tmp_path / artifact).exists(), f"dry-run wrote {artifact}"

    # Running --dry-run twice in a row is equally side-effect-free.
    result2 = _run_topgen(
        capsys, "gen-top", str(DESIGN_YML),
        "--mode", "verilog",
        "--output", str(output),
        "--build-dir", str(tmp_path / "build"),
        "--dry-run",
    )
    assert result2.returncode == 0
    assert _tree_snapshot(tmp_path) == tree_before
    assert not output.exists()

    # A real (non-dry-run) invocation afterwards still generates everything,
    # including ip_info.yaml, proving dry-run didn't leave the design in a
    # state that skips required generation.
    result3 = _run_topgen(
        capsys, "gen-top", str(DESIGN_YML),
        "--mode", "verilog",
        "--output", str(output),
        "--build-dir", str(tmp_path / "build"),
    )
    assert result3.returncode == 0, result3.stdout + result3.stderr
    assert output.exists()
    assert (tmp_path / "ip_info.yaml").exists()


def test_gen_top_missing_design_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.yml"

    result = _run_topgen(
        capsys, "gen-top", str(missing),
        "--mode", "verilog",
        "--output", str(tmp_path / "algo_top.v"),
    )

    assert result.returncode == 1
    assert "Design file not found" in result.stdout
    assert "Traceback" not in result.stderr


def test_gen_top_requires_mode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["topgen", "gen-top", str(DESIGN_YML)])

    assert exc_info.value.code == 2
    assert "--mode" in capsys.readouterr().err


def _write_two_clock_domain_design(tmp_path: Path, *, cdc_block: str = "") -> tuple[Path, Path]:
    """A synthetic two-RTL-module design whose contracts declare different
    clock raw ports (clk_a/clk_b) — resolved purely from contracts, no
    build artifacts needed (same contracts-only synthesis path
    test_ir_build.py's trigger_demo tests already rely on)."""
    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n"
        "  ip_info_key: src\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk_a, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst, direction: input, width: 1}\n"
        "    dout: {raw_port: dout, direction: output, width: 8}\n"
    )
    (tmp_path / "interfaces" / "dst.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: dst\n"
        "  ip_info_key: dst\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk_b, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst, direction: input, width: 1}\n"
        "    din: {raw_port: din, direction: input, width: 8}\n"
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
        "    interface_contract: interfaces/dst.interface.yaml\n"
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
        f"    {cdc_block}\n"
    )
    return design_yml, modules_yml


def test_gen_top_strict_fails_on_undeclared_clock_crossing(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    design_yml, modules_yml = _write_two_clock_domain_design(tmp_path)

    result = _run_topgen(
        capsys, "gen-top", str(design_yml),
        "--mode", "verilog",
        "--output", str(tmp_path / "algo_top.v"),
        "--contracts-from", str(modules_yml),
        "--strict",
    )

    assert result.returncode == 1
    assert "undeclared clock/reset domain crossing" in result.stdout
    assert "clk_a" in result.stdout and "clk_b" in result.stdout


def test_gen_top_strict_passes_with_declared_cdc_adapter(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    design_yml, modules_yml = _write_two_clock_domain_design(
        tmp_path, cdc_block="cdc: {kind: 2ff_sync}",
    )

    result = _run_topgen(
        capsys, "gen-top", str(design_yml),
        "--mode", "verilog",
        "--output", str(tmp_path / "algo_top.v"),
        "--contracts-from", str(modules_yml),
        "--strict",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_clean_dry_run_lists_without_deleting(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    output = tmp_path / "algo_top.v"
    _run_topgen(
        capsys, "gen-top", str(DESIGN_YML), "--mode", "verilog",
        "--output", str(output), "--build-dir", str(tmp_path / "build"),
    )

    result = _run_topgen(
        capsys, "clean", str(DESIGN_YML), "--output", str(output),
        "--no-verify", "--dry-run",
    )

    assert result.returncode == 0
    assert "Dry run only; no files were removed" in result.stdout
    assert output.exists()


def test_clean_removes_generated_artifacts(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    output = tmp_path / "algo_top.v"
    _run_topgen(
        capsys, "gen-top", str(DESIGN_YML), "--mode", "verilog",
        "--output", str(output), "--build-dir", str(tmp_path / "build"),
    )
    assert output.exists()

    result = _run_topgen(
        capsys, "clean", str(DESIGN_YML), "--output", str(output), "--no-verify",
    )

    assert result.returncode == 0
    assert "Removed" in result.stdout
    assert not output.exists()


@pytest.mark.parametrize("subcmd", ["gen-top", "validate", "ip-summary", "match-ports"])
def test_missing_design_arg_is_argparse_error(capsys: pytest.CaptureFixture[str], subcmd: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["topgen", subcmd])

    assert exc_info.value.code == 2
    assert "usage:" in capsys.readouterr().err.lower()
