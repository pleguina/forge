"""Coverage for the single configuration path of a ``forge.yml`` project.

User intent lives in ``forge.yml`` (or files it names), everything under
``.forge/`` is derived and rewritten by every command, and ``forge build``,
``forge test``, ``forge report`` and ``forge inspect`` all find the project
on their own.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.project.adopt import plan_adoption
from forge.project.config import ForgeConfig, ModulePolicy
from forge.project.resolve import resolve_project
from forge.project.status import PASS, PARTIAL, evaluate

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"
REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_VERIFY = (
    REPO_ROOT / "plugins" / "passthrough_demo" / "forge" / "verify" / "design.verification.yml"
)


def _run(capsys: pytest.CaptureFixture[str], argv: list):
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(argv)
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, root)
    plan_adoption(root).write()
    monkeypatch.chdir(root)
    return root


def _set_module(root: Path, name: str, policy: ModulePolicy) -> None:
    config = ForgeConfig.load(root)
    config.modules[name] = policy
    config.save()


# ── forge.yml ─────────────────────────────────────────────────────────────

def test_a_latency_in_forge_yml_reaches_the_registry(project: Path) -> None:
    _set_module(project, "shaper", ModulePolicy(latency={"kind": "fixed", "cycles": 2}))

    registry = yaml.safe_load(resolve_project(project).registry.read_text())
    shaper = next(m for m in registry["modules"] if m["name"] == "shaper")

    assert shaper["latency"] == {"kind": "fixed", "cycles": 2}


def test_a_bare_cycle_count_is_a_fixed_latency(tmp_path: Path) -> None:
    (tmp_path / "forge.yml").write_text("modules:\n  shaper:\n    latency: 3\n")

    loaded = ForgeConfig.load(tmp_path)

    assert loaded.modules["shaper"].latency == {"kind": "fixed", "cycles": 3}
    assert loaded.to_dict()["modules"]["shaper"]["latency"] == 3


def test_a_malformed_latency_is_refused(tmp_path: Path) -> None:
    (tmp_path / "forge.yml").write_text(
        "modules:\n  shaper:\n    latency: {kind: bounded, min_cycles: 4}\n")

    with pytest.raises(ValueError, match="shaper"):
        ForgeConfig.load(tmp_path)


def test_declaring_every_latency_in_forge_yml_settles_the_latency_check(project: Path) -> None:
    status = evaluate(project)
    assert status.section("Latency model").state == PARTIAL
    assert all(".forge" not in (d.action or "") for d in status.warnings)

    for unit in status.discovery.managed_units:
        _set_module(project, unit.name, ModulePolicy(latency={"kind": "fixed", "cycles": 1}))

    assert evaluate(project).section("Latency model").state == PASS


def test_inferred_contracts_need_no_review(project: Path) -> None:
    status = evaluate(project)

    assert status.section("Contracts").state == PASS
    assert "ATG035" not in [d.code for d in status.warnings + status.blockers]


def test_a_contract_named_in_forge_yml_replaces_the_inferred_one(project: Path) -> None:
    own = project / "shaper.interface.yaml"
    shutil.copy(project / ".forge/contracts/shaper.interface.yaml", own)
    _set_module(project, "shaper", ModulePolicy(contract="shaper.interface.yaml"))

    resolved = resolve_project(project)
    registry = yaml.safe_load(resolved.registry.read_text())
    shaper = next(m for m in registry["modules"] if m["name"] == "shaper")

    assert (resolved.paths.project_config_root / shaper["interface_contract"]).resolve() == own
    assert not (resolved.paths.contract_root / "shaper.interface.yaml").exists()


def test_re_adopting_keeps_every_decision_forge_yml_records(project: Path) -> None:
    config = ForgeConfig.load(project)
    config.part = "xcvu13p-flga2577-1-e"
    config.clock.frequency = "360MHz"
    config.verification_config = "verify/design.verification.yml"
    config.modules["shaper"] = ModulePolicy(latency={"kind": "fixed", "cycles": 2})
    config.save()

    again = plan_adoption(project, part=None).config

    assert again.part == "xcvu13p-flga2577-1-e"
    assert again.clock.frequency == "360MHz"
    assert again.verification_config == "verify/design.verification.yml"
    assert again.modules["shaper"].latency == {"kind": "fixed", "cycles": 2}


# ── .forge/ is derived ────────────────────────────────────────────────────

def test_a_deleted_state_directory_is_written_again(project: Path) -> None:
    shutil.rmtree(project / ".forge")

    resolved = resolve_project(project)

    assert resolved.design.is_file()
    assert resolved.registry.is_file()


def test_the_project_is_found_from_a_subdirectory(project: Path) -> None:
    assert resolve_project(project / "rtl").root == project


def test_outside_a_project_the_error_says_how_to_start_one(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="forge adopt"):
        resolve_project(tmp_path)


# ── Bare commands ─────────────────────────────────────────────────────────

def test_bare_build_generates_the_projects_top_level(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, _out, err = _run(capsys, ["build"])

    assert code == 0, err
    assert (project / ".forge/generated/algo_top.v").is_file()


def test_build_json_stays_one_document_while_generating(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = _run(capsys, ["build", "--json"])

    assert code == 0, err
    payload = json.loads(out)
    assert payload["status"] == "pass"
    assert (project / ".forge/generated/algo_top.v").is_file()


def test_bare_build_dry_run_writes_no_top_level(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, _out, err = _run(capsys, ["build", "--dry-run"])

    assert code == 0, err
    assert not (project / ".forge/generated/algo_top.v").exists()


def test_bare_inspect_and_report_use_the_project(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run(capsys, ["inspect", "--json"])[0] == 0

    code, _out, err = _run(capsys, ["report"])

    assert code == 0, err
    assert (project / ".forge/reports/latency_check.md").is_file()


def test_bare_test_without_a_verification_config_says_what_to_set(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, _out, err = _run(capsys, ["test"])

    assert code == 1
    assert "verification.config" in err


def test_a_flow_without_its_simulator_is_skipped_not_passed(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import forge.core.toolchain_versions as toolchain

    shutil.copy(PASSTHROUGH_VERIFY, project / "design.verification.yml")
    config = ForgeConfig.load(project)
    config.verification_config = "design.verification.yml"
    config.save()
    monkeypatch.setattr(toolchain, "tool_present", lambda name: False)

    code, out, err = _run(capsys, ["test", "--json"])

    assert code == 0, err
    payload = json.loads(out)
    assert payload["status"] == "warn"
    assert payload["metrics"]["flows_passed"] == []
    assert payload["metrics"]["flows_skipped"]


def test_init_without_a_simulator_does_not_report_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import forge.core.toolchain_versions as toolchain

    monkeypatch.setattr(toolchain, "tool_present", lambda name: False)

    code, out, err = _run(capsys, [
        "init", "throwaway_plugin",
        "--plugins-root", str(tmp_path / "plugins"), "--consumer-root", str(tmp_path),
        "--json",
    ])

    assert code == 0, err
    payload = json.loads(out)
    assert payload["status"] == "warn"
    assert payload["metrics"]["steps_skipped"] == ["test-run"]


# ── A design named in forge.yml ───────────────────────────────────────────

PASSTHROUGH = REPO_ROOT / "plugins" / "passthrough_demo" / "forge"


def _snapshot(root: Path) -> dict:
    return {p: p.stat().st_mtime for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def authored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "authored"
    root.mkdir()
    (root / "forge.yml").write_text(
        f"project: authored\n"
        f"design: {PASSTHROUGH / 'designs' / 'design.yml'}\n"
        f"verification:\n  config: {PASSTHROUGH / 'verify' / 'design.verification.yml'}\n"
    )
    monkeypatch.chdir(root)
    return root


def test_an_authored_design_is_used_as_it_stands(authored: Path) -> None:
    resolved = resolve_project(authored)

    assert resolved.design == PASSTHROUGH / "designs" / "design.yml"
    assert resolved.registry == PASSTHROUGH / "modules.yml"
    assert not (authored / ".forge" / "project").exists()


def test_a_missing_authored_design_is_reported(tmp_path: Path) -> None:
    (tmp_path / "forge.yml").write_text("design: nope/design.yml\n")

    with pytest.raises(FileNotFoundError, match="nope/design.yml"):
        resolve_project(tmp_path)


def test_an_unknown_build_option_is_refused(tmp_path: Path) -> None:
    (tmp_path / "forge.yml").write_text("build:\n  modes: verilog\n")

    with pytest.raises(ValueError, match="modes"):
        ForgeConfig.load(tmp_path)


def test_bare_build_of_an_authored_design_writes_only_under_forge(
    authored: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    before = _snapshot(PASSTHROUGH)
    with (authored / "forge.yml").open("a") as handle:
        handle.write("build:\n  top_name: pt_top\n")

    code, _out, err = _run(capsys, ["build"])

    assert code == 0, err
    assert (authored / ".forge/generated/pt_top.v").is_file()
    assert _snapshot(PASSTHROUGH) == before


def test_check_on_an_authored_design_validates_it_instead_of_scanning(
    authored: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    status = evaluate(authored)

    assert status.discovery is None
    assert status.section("Design").state == PASS
    assert status.section("Latency model").state == PASS
    assert status.section("Verification").state == PASS
    assert status.recommended_action.command == "forge build"

    assert _run(capsys, ["build"])[0] == 0
    assert evaluate(authored).section("Generated artifacts").state == PASS


def test_an_authored_design_that_does_not_validate_fails_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = tmp_path / "design.yml"
    design.write_text(
        (PASSTHROUGH / "designs" / "design.yml").read_text()
        .replace("registry: ../modules.yml", f"registry: {PASSTHROUGH / 'modules.yml'}")
        .replace("ref: passthrough", "ref: no_such_module")
    )
    (tmp_path / "forge.yml").write_text("design: design.yml\n")

    status = evaluate(tmp_path)

    assert status.section("Design").state == "FAIL"
    assert "ATG015" in [d.code for d in status.blockers]


def test_explaining_a_module_of_an_authored_design_points_at_inspect(authored: Path) -> None:
    from forge.project.explain import UnknownTarget, explain

    with pytest.raises(UnknownTarget, match="forge inspect"):
        explain("pt", authored)


def test_bare_test_builds_the_design_a_flow_names_before_running_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import forge.core.cli._shared as shared
    import forge.core.toolchain_versions as toolchain

    root = tmp_path / "flows"
    (root / "verify").mkdir(parents=True)
    contract = yaml.safe_load((PASSTHROUGH / "verify" / "design.verification.yml").read_text())
    contract["datasets"] = {
        name: {**dataset, "xml": str((PASSTHROUGH / "verify" / dataset["xml"]).resolve())}
        for name, dataset in contract["datasets"].items()
    }
    contract["flows"] = [
        {**contract["flows"][0], "dut_rtl_source": "sim/dut",
         "design": str(PASSTHROUGH / "designs" / "design.yml")},
    ]
    (root / "verify" / "design.verification.yml").write_text(yaml.safe_dump(contract))
    (root / "forge.yml").write_text(
        f"design: {PASSTHROUGH / 'designs' / 'design.yml'}\n"
        "build:\n  top_name: pt_top\n"
        "verification:\n  config: verify/design.verification.yml\n"
    )
    monkeypatch.chdir(root)
    monkeypatch.setattr(toolchain, "tool_present", lambda name: True)

    real = shared.run_forge_command
    steps = []

    def run(argv, *, quiet):
        steps.append(argv[:2])
        if argv[0] == "build":
            return real(argv, quiet=quiet)
        return 0, ""

    monkeypatch.setattr(shared, "run_forge_command", run)

    code, _out, err = _run(capsys, ["test"])

    assert code == 0, err
    assert (root / "sim" / "dut" / "algo_top.v").is_file()
    assert (root / "sim" / "dut" / "port_map.yaml").is_file()
    flow_build = steps.index(["build", str(PASSTHROUGH / "designs" / "design.yml")])
    assert steps[flow_build + 1] == ["test", "prepare"]


@pytest.mark.parametrize("error, code", [
    (FileNotFoundError("XML dataset not found: part000.xml"), 1),
    (RuntimeError("a bug"), 2),
])
def test_a_chained_command_that_raises_reports_like_forge_itself(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    error: Exception, code: int,
) -> None:
    import forge.core.cli.groups.report as report
    from forge.core.cli._shared import run_forge_command

    def raise_(args):
        raise error

    monkeypatch.setattr(report, "cmd_report", raise_)

    assert run_forge_command(["report"], quiet=True)[0] == code
    assert str(error) in capsys.readouterr().err
