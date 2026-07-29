"""Real end-to-end xsim coverage for `forge verify run` / `prepare`.

test_verify_cli_commands.py deliberately stayed away from `run`/`generate`/
`prepare` because they need a full gen-top'd canonical layout and a real
simulator — this file is that follow-up. It copies
plugins/passthrough_demo into an isolated tmp_path "consumer root" (so nothing
tracked in git is ever touched or mutated), regenerates its topology and
verify artifacts there, and drives an actual xvlog → xelab → xsim run
through forge.verify.__main__.main(), in-process so pytest-cov credits the
executed lines. Skipped automatically when Vivado's xsim toolchain isn't on
PATH.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
import forge.verify.__main__ as verify_cli

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
