"""Real end-to-end Verilator coverage for `forge verify run`.

Mirrors test_verify_run_xsim.py's convention exactly: tool-gated skip (real
`shutil.which`, never mocked), a `shutil.copytree` of plugins/passthrough_demo
into an isolated tmp_path "consumer root", real `gen-top` + real `generate` +
real `gen_stimulus.py` + a real verilator build-and-run through
forge.verification.__main__.main() in-process.

passthrough_demo's design.verification.yml declares a second flow,
`passthrough_verilator` (same DUT/TB/dataset as `passthrough_xsim`, only the
backend differs), added specifically to give this backend a real end-to-end
target — proving `full_chip_rtl` is genuinely multi-backend, not just
matrix-declared.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
import forge.verification.__main__ as verify_cli

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SRC = REPO_ROOT / "plugins/passthrough_demo"

VERILATOR_AVAILABLE = shutil.which("verilator") is not None
skip_without_verilator = pytest.mark.skipif(
    not VERILATOR_AVAILABLE, reason="verilator not on PATH"
)


@pytest.fixture
def consumer_root(tmp_path: Path) -> Path:
    """An isolated copy of plugins/passthrough_demo, gen-top'd into place.

    Same layout test_verify_run_xsim.py's fixture builds; both flows
    (passthrough_xsim, passthrough_verilator) share one DUT/gen-top output.
    """
    dest_plugin = tmp_path / "plugins" / "passthrough_demo"
    shutil.copytree(
        PLUGIN_SRC, dest_plugin, ignore=shutil.ignore_patterns("__pycache__")
    )

    parser = build_parser()
    parsed = parser.parse_args([
        "topgen", "gen-top",
        str(dest_plugin / "forge/designs/design.yml"),
        "--mode", "verilog",
        "--consumer-root", str(tmp_path),
        "--output", str(tmp_path / "gen-top/design_passthrough_demo/algo_top.v"),
        "--build-dir", str(tmp_path / "build/passthrough_demo"),
    ])
    parsed.func(parsed)  # raises SystemExit only on failure; let it propagate

    return tmp_path


def _run_verify_inprocess(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str
):
    monkeypatch.setattr(sys, "argv", ["forge-verify", *args])
    code = 0
    try:
        verify_cli.main()
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _generate_and_write_stimulus(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> Path:
    """Run `generate`, then the plugin's own gen_stimulus.py for the verilator
    flow (mirroring the documented, real plugin workflow — `generate` alone
    never writes stimulus_current.svh; that is a separate, plugin-owned step
    per design.verification.yml's own header comment)."""
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    code, out, err = _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )
    assert code == 0, out + err

    flow_dir = consumer_root / "plugins/passthrough_demo/forge/verify/passthrough_verilator"
    tools_dir = consumer_root / "plugins/passthrough_demo/forge/verify/tools"
    sys.path.insert(0, str(tools_dir))
    try:
        for name in ("gen_stimulus",):
            sys.modules.pop(name, None)
        import gen_stimulus  # noqa: PLC0415
        gen_stimulus.generate_for_flow(
            "passthrough_verilator", 0, flow_dir / "stimulus_current.svh"
        )
    finally:
        sys.path.remove(str(tools_dir))
        sys.modules.pop("gen_stimulus", None)

    flow_yml = flow_dir / "verify.flow.yml"
    assert flow_yml.exists()
    return flow_yml


@skip_without_verilator
def test_generate_writes_verilator_flow_artifacts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"

    code, out, _err = _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )

    assert code == 0
    flow_dir = consumer_root / "plugins/passthrough_demo/forge/verify/passthrough_verilator"
    flow_yml_path = flow_dir / "verify.flow.yml"
    assert flow_yml_path.exists()
    assert (flow_dir / "tb_algo_top.sv").exists()

    import yaml
    flow_doc = yaml.safe_load(flow_yml_path.read_text())
    assert flow_doc["flow"]["backend"] == "verilator"


@skip_without_verilator
def test_run_executes_real_verilator_simulation_and_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    flow_yml = _generate_and_write_stimulus(monkeypatch, capsys, consumer_root)

    code, out, _err = _run_verify_inprocess(
        monkeypatch, capsys, "run", str(flow_yml),
        "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root),
    )

    assert code == 0, out
    assert "[preflight] All artifact checks passed." in out
    assert "[verilator 1/2] Compiling+Elaborating" in out
    assert "[verilator 2/2] Simulating" in out
    assert "[run] backend: verilator" in out

    simulate_log = flow_yml.parent / "xsim_work/simulate.log"
    assert simulate_log.exists()
    assert "TB PASS: passthrough_verilator completed" in simulate_log.read_text()


@skip_without_verilator
def test_run_missing_rtl_fails_preflight_without_running_verilator(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    """Sanity check that a broken canonical layout is actually caught, not
    silently accepted — delete the gen-top'd RTL and confirm `run` fails at
    preflight instead of reporting a false PASS (same negative-control
    convention as test_verify_run_xsim.py)."""
    flow_yml = _generate_and_write_stimulus(monkeypatch, capsys, consumer_root)
    (consumer_root / "gen-top/design_passthrough_demo/algo_top.v").unlink()

    code, out, err = _run_verify_inprocess(
        monkeypatch, capsys, "run", str(flow_yml),
        "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root),
    )

    assert code != 0
    assert "verilator 2/2] Simulating" not in out
    assert "DUT RTL not found" in (out + err)


@skip_without_verilator
def test_run_backend_populates_enriched_execution_result_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    """The enriched ExecutionResult fields, proven for a second,
    independent backend — backend_id/duration_s/stage are real, and
    the waveform-capability claim is empirically honest: no .fst file exists
    for this backend in this slice, so capabilities.supports_waveform is
    False and waveform_path stays None — never fabricated."""
    flow_yml = _generate_and_write_stimulus(monkeypatch, capsys, consumer_root)

    from forge.verification.backend_registry import get_adapter
    from forge.verification.execution_stage import ExecutionStage
    from forge.verification.flow_loader import load_generic_flow
    from forge.verification.runtime_context import RuntimeContext

    assert verify_cli._bootstrap("passthrough_demo", str(flow_yml))
    cfg = load_generic_flow(flow_yml, consumer_root=consumer_root)
    adapter = get_adapter(cfg.backend)
    assert adapter.backend_id == "verilator"

    ctx = RuntimeContext(work_dir=flow_yml.parent / "xsim_work")
    adapter.prepare_backend_inputs(cfg, ctx)
    result = adapter.run_backend(cfg, ctx)

    assert result.success, result.backend_metadata
    assert result.backend_id == "verilator"
    assert result.stage == ExecutionStage.SIMULATE
    assert result.duration_s is not None and result.duration_s > 0

    # The waveform-capability claim must stay consistent: no real .fst file
    # is produced by this backend in this slice (see backend_verilator.py's
    # module docstring), so the capability is honestly False and no path is
    # fabricated.
    assert adapter.capabilities.supports_waveform is False
    assert result.waveform_path is None
    assert not list(ctx.work_dir.rglob("*.fst"))


@skip_without_verilator
def test_run_backend_compile_failure_reports_compile_stage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    """A real verilator build failure (broken SV syntax) must report
    ``stage == ExecutionStage.COMPILE`` — Verilator has no separate
    elaborate step, unlike xsim, so this is the only failure stage short
    of PREFLIGHT."""
    flow_yml = _generate_and_write_stimulus(monkeypatch, capsys, consumer_root)
    tb_sv = flow_yml.parent / "tb_algo_top.sv"
    original = tb_sv.read_text()
    tb_sv.write_text(original + "\nthis is not valid systemverilog {{{\n")

    from forge.verification.backend_registry import get_adapter
    from forge.verification.execution_stage import ExecutionStage
    from forge.verification.flow_loader import load_generic_flow
    from forge.verification.runtime_context import RuntimeContext

    assert verify_cli._bootstrap("passthrough_demo", str(flow_yml))
    cfg = load_generic_flow(flow_yml, consumer_root=consumer_root)
    adapter = get_adapter(cfg.backend)
    ctx = RuntimeContext(work_dir=flow_yml.parent / "xsim_work")

    adapter.prepare_backend_inputs(cfg, ctx)
    result = adapter.run_backend(cfg, ctx)

    assert result.success is False
    assert result.stage == ExecutionStage.COMPILE
    assert result.duration_s is not None and result.duration_s > 0
    assert result.backend_id == "verilator"
