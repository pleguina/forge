"""pytest conftest for plugins/trigger_demo/verify/tools/tests.

Adds the framework Python package (fw_verify) to sys.path, then loads all
plugin-local modules (bootstrap, gen_stimulus) by file path so that
sys.modules["bootstrap"] and sys.modules["gen_stimulus"] are always this
plugin's versions — regardless of order or other plugin suites in the session.
"""
import importlib.util as _ilu
import sys
from pathlib import Path

_TOOLS = Path(__file__).parent.parent.resolve()
_FW_PYTHON = _TOOLS.parents[3] / "framework" / "verify" / "python"

# Only add fw_verify to sys.path; do NOT add _TOOLS so that bare-name imports
# of gen_stimulus/bootstrap cannot accidentally resolve from the wrong plugin.
_fw_str = str(_FW_PYTHON)
if _fw_str not in sys.path:
    sys.path.insert(0, _fw_str)


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
