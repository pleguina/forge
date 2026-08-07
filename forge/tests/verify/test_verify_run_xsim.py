"""Real end-to-end xsim coverage for `forge verify run` / `prepare`.

test_verify_cli_commands.py deliberately stayed away from `run`/`generate`/
`prepare` because they need a full gen-top'd canonical layout and a real
simulator — this file is that follow-up. It copies
plugins/passthrough_demo into an isolated tmp_path "consumer root" (so nothing
tracked in git is ever touched or mutated), regenerates its topology and
verify artifacts there, and drives an actual xvlog → xelab → xsim run
through forge.verification.__main__.main(), in-process so pytest-cov credits the
executed lines. Skipped automatically when Vivado's xsim toolchain isn't on
PATH.
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

XSIM_AVAILABLE = all(shutil.which(tool) for tool in ("xvlog", "xelab", "xsim"))
skip_without_xsim = pytest.mark.skipif(
    not XSIM_AVAILABLE, reason="Vivado xsim toolchain (xvlog/xelab/xsim) not on PATH"
)


@pytest.fixture
def consumer_root(tmp_path: Path) -> Path:
    """An isolated copy of plugins/passthrough_demo, gen-top'd into place.

    Mirrors the real, documented workflow (see the header comment of
    plugins/passthrough_demo/forge/verify/design.verification.yml) but
    rooted at tmp_path instead of the repo, so `forge topgen gen-top` and
    `forge verify generate/run` write only into scratch space.
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


@skip_without_xsim
def test_generate_writes_flow_artifacts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"

    code, out, _err = _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )

    assert code == 0
    assert "Generated" in out
    flow_dir = consumer_root / "plugins/passthrough_demo/forge/verify/passthrough_xsim"
    assert (flow_dir / "verify.flow.yml").exists()
    assert (flow_dir / "tb_algo_top.sv").exists()


@skip_without_xsim
def test_prepare_generates_validates_and_checks_layout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"

    code, out, _err = _run_verify_inprocess(
        monkeypatch, capsys, "prepare", str(design_yml),
        "--consumer-root", str(consumer_root),
    )

    assert code == 0
    assert "[prepare] Step 1/3 OK" in out
    assert "[prepare] Step 2/3 OK" in out
    assert "[prepare] Step 3/3 OK" in out
    assert "All checks passed" in out


@skip_without_xsim
def test_run_executes_real_xsim_simulation_and_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )

    flow_yml = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim/verify.flow.yml"
    )

    code, out, _err = _run_verify_inprocess(
        monkeypatch, capsys, "run", str(flow_yml),
        "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root),
    )

    assert code == 0, out
    assert "[preflight] All artifact checks passed." in out
    assert "[xsim 3/3] Simulating" in out

    simulate_log = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim"
        / "xsim_work/simulate.log"
    )
    assert simulate_log.exists()
    assert "TB PASS: passthrough_xsim completed" in simulate_log.read_text()


@skip_without_xsim
def test_run_backend_populates_enriched_execution_result_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    """ExecutionResult.duration_s/waveform_path/backend_id/stage
    are real, non-fabricated values — proven by calling the real adapter directly
    (not mocked) after a real `generate`, then inspecting the returned dataclass."""
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )

    flow_yml = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim/verify.flow.yml"
    )

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

    assert result.success, result.backend_metadata
    assert result.backend_id == "xsim"
    assert result.stage == ExecutionStage.SIMULATE
    assert result.duration_s is not None and result.duration_s > 0
    assert result.waveform_path is not None
    assert result.waveform_path.exists()
    assert result.waveform_path.suffix == ".wdb"


@skip_without_xsim
def test_run_backend_compile_failure_reports_compile_stage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    """A real xvlog failure (broken SV syntax) must report
    ``stage == ExecutionStage.COMPILE`` — not a hardcoded/fabricated stage,
    the direct fix for `_tail_of_log` always assuming the simulate stage."""
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )

    flow_yml = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim/verify.flow.yml"
    )
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
    assert result.backend_id == "xsim"


@skip_without_xsim
def test_run_missing_rtl_fails_preflight_without_running_xsim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    """Sanity check that a broken canonical layout is actually caught,
    not silently accepted — delete the gen-top'd RTL and confirm `run`
    fails at preflight instead of reporting a false PASS."""
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )
    (consumer_root / "gen-top/design_passthrough_demo/algo_top.v").unlink()

    flow_yml = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim/verify.flow.yml"
    )
    code, out, err = _run_verify_inprocess(
        monkeypatch, capsys, "run", str(flow_yml),
        "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root),
    )

    assert code != 0
    assert "xsim 3/3] Simulating" not in out
    assert "DUT RTL not found" in (out + err)


@skip_without_xsim
def test_json_dataset_format_parity_real_run_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    """A real simulation run driven by stimulus generated from the JSON
    dataset sibling (JsonDatasetLoader) produces the same real pass as
    the XML-driven one — format parity proven by an actual xsim run, not
    just "the loader doesn't crash."""
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )

    flow_dir = consumer_root / "plugins/passthrough_demo/forge/verify/passthrough_xsim"
    tools_dir = consumer_root / "plugins/passthrough_demo/forge/verify/tools"
    golden_json = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/schemas/data/passthrough_demo_golden.json"
    )
    assert golden_json.exists()

    sys.path.insert(0, str(tools_dir))
    try:
        sys.modules.pop("gen_stimulus", None)
        import gen_stimulus  # noqa: PLC0415

        gen_stimulus.generate_for_flow(
            "passthrough_xsim", 0, flow_dir / "stimulus_current.svh",
            dataset_path=golden_json,
        )
    finally:
        sys.path.remove(str(tools_dir))
        sys.modules.pop("gen_stimulus", None)

    assert "8'h3A" in (flow_dir / "stimulus_current.svh").read_text()

    flow_yml = flow_dir / "verify.flow.yml"
    code, out, _err = _run_verify_inprocess(
        monkeypatch, capsys, "run", str(flow_yml),
        "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root),
    )

    assert code == 0, out
    simulate_log = flow_dir / "xsim_work/simulate.log"
    assert simulate_log.exists()
    assert "TB PASS: passthrough_xsim completed" in simulate_log.read_text()


@skip_without_xsim
def test_identity_dataset_adapter_drives_a_real_passing_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], consumer_root: Path
) -> None:
    """passthrough_demo's real `passthrough.identity-xml`
    adapter, resolved by explicit id (never suffix), `materialize()`s a
    real layer-A SerializedDataset into a CanonicalDataset that is a
    genuine no-op — then drives an actual xsim run from that
    adapter-produced data, proving the layer-B protocol is real and wired
    end to end, not just object-equal on paper."""
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    _run_verify_inprocess(
        monkeypatch, capsys, "generate", str(design_yml),
        "--consumer-root", str(consumer_root),
    )

    flow_dir = consumer_root / "plugins/passthrough_demo/forge/verify/passthrough_xsim"
    golden_xml = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/schemas/data/passthrough_demo_golden.xml"
    )

    assert verify_cli._bootstrap("passthrough_demo", str(flow_dir / "verify.flow.yml"))

    from forge.verification.dataset_adapter import DatasetSource, get_dataset_adapter
    from forge.verification.dataset_format import XmlDatasetLoader

    direct = XmlDatasetLoader().load(golden_xml)
    adapter = get_dataset_adapter("passthrough.identity-xml")
    canonical = adapter.materialize(DatasetSource(serialized=direct), {})

    # The genuine-no-op proof: adapter output is byte-identical to the
    # direct layer-A loader path (the same one gen_stimulus.py itself now
    # uses) — not silently dropping or altering data.
    assert canonical.events == direct.events
    assert canonical.metadata == direct.metadata

    # Drive a real simulation from the adapter-produced CanonicalDataset —
    # the same field extraction gen_stimulus.py uses, but built here from
    # `canonical.events` specifically, to prove the adapter's own output
    # (not just the direct loader's) is what generated this real pass.
    from forge.verification.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

    ev = dict(zip(canonical.metadata.event_ids, canonical.events))["0"]
    data_in       = int(ev["in"]["data_in"], 0)
    data_in_valid = int(ev["in"]["data_in_valid"], 0)
    data_out       = int(ev["golden"]["data_out"], 0)
    data_out_valid = int(ev["golden"]["data_out_valid"], 0)

    em = StimulusEmitter()
    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()
    with em.event_block("event_0"):
        em.drive("pt_data_in", data_in, width=8)
        em.drive("pt_data_in_valid", data_in_valid, width=1)
        em.tick()
        em.tick()
    with em.event_block("checks"):
        em.check("pt_data_out", data_out, width=8, label="data_out_check", event_id=0)
        em.check("pt_data_out_valid", data_out_valid, width=1, label="data_out_valid_check", event_id=0)
    with em.event_block("drain"):
        em.drive("pt_data_in_valid", 0, width=1)
        em.tick(cycles=2)
    write_run_stimulus_svh(em, flow_dir / "stimulus_current.svh", header_comment="adapter-driven event=0")

    flow_yml = flow_dir / "verify.flow.yml"
    code, out, _err = _run_verify_inprocess(
        monkeypatch, capsys, "run", str(flow_yml),
        "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root),
    )

    assert code == 0, out
    simulate_log = flow_dir / "xsim_work/simulate.log"
    assert "TB PASS: passthrough_xsim completed" in simulate_log.read_text()
    assert "FORGE_CHECK|check_id=0:data_out_check" in simulate_log.read_text()
