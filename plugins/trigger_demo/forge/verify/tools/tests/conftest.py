"""pytest conftest for plugins/trigger_demo/forge/verify/tools/tests.

Loads all plugin-local modules (bootstrap, gen_stimulus) by file path so
that sys.modules["bootstrap"] and sys.modules["gen_stimulus"] are always
this plugin's versions — regardless of order or other plugin suites in
the session. Assumes the ``forge`` package is installed (pip install -e
forge/), so forge.verification is importable without any sys.path surgery.
"""
import contextlib
import importlib.util as _ilu
import io
import shutil
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).parent.parent.resolve()


def _load_by_path(module_name: str, file_path: Path) -> None:
    """Load a module from an explicit file path, overriding any cached version."""
    spec = _ilu.spec_from_file_location(module_name, str(file_path))
    mod = _ilu.module_from_spec(spec)   # type: ignore[union-attr]
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)        # type: ignore[union-attr]


# Load bootstrap and gen_stimulus from THIS plugin's tools/, unconditionally
# overwriting any same-named module already cached from another plugin.
_load_by_path("bootstrap",    _TOOLS / "bootstrap.py")
_load_by_path("gen_stimulus", _TOOLS / "gen_stimulus.py")

import bootstrap as _td_bootstrap  # noqa: E402
_td_bootstrap.bootstrap()


@pytest.fixture(scope="session")
def generated_verify_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of this plugin's verify/ directory with the flow files
    ``forge dev verify generate`` writes and the stimulus gen_stimulus.py
    writes; the repository keeps neither. trigger_pipeline_xsim gets no
    testbench: it is generated from RTL, which needs an HLS build."""
    import gen_stimulus
    from forge.verification.prepare import generate_flows

    root = tmp_path_factory.mktemp("trigger_demo") / "verify"
    shutil.copytree(
        _TOOLS.parent, root, ignore=shutil.ignore_patterns("__pycache__", "tests", "xsim_work"),
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        generate_flows(root / "design.verification.yml", consumer_root=_TOOLS.parents[4])
        gen_stimulus.generate_all(root / "schemas/data/trigger_demo_golden.xml", root)
    return root
