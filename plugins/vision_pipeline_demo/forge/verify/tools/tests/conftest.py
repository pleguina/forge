"""pytest conftest for plugins/vision_pipeline_demo/forge/verify/tools/tests.

Loads this plugin's own ``bootstrap``/``dataset_adapter``/
``golden_model_provider`` by file path (mirroring
plugins/trigger_demo/forge/verify/tools/tests/conftest.py's own precedent)
so they are always THIS plugin's versions, regardless of import order or
other plugin suites in the same pytest session. Assumes the ``forge``
package is installed (``pip install -e forge/``), so ``forge.verification`` is
importable without any sys.path surgery.
"""
import importlib.util as _ilu
import sys
from pathlib import Path

_TOOLS = Path(__file__).parent.parent.resolve()

# vision_pipeline_demo's own bootstrap.py bare-imports `dataset_adapter`/
# `golden_model_provider` (matching how forge's real plugin loader adds a
# plugin's tools/ dir to sys.path before calling its bootstrap() -- see
# plugin_registry.py's own "sys.path when bootstrap_plugin() is called"
# note) -- so _TOOLS must be on sys.path here too. Safe to do for this
# specific directory (unlike the plugin root): forge/verify/tools/ has no
# `forge`-named subdirectory of its own to collide with the real `forge`
# package.
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


def _load_by_path(module_name: str, file_path: Path) -> None:
    """Load a module from an explicit file path, overriding any cached version."""
    spec = _ilu.spec_from_file_location(module_name, str(file_path))
    mod = _ilu.module_from_spec(spec)   # type: ignore[union-attr]
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)        # type: ignore[union-attr]


# Load bootstrap (which self-loads dataset_adapter/golden_model_provider by
# bare name, then self-registers the plugin's datasets adapters, this
# plugin's `datasets` package -- see dataset_adapter.py's own
# _load_datasets_package() -- and its golden-model providers) from THIS
# plugin's tools/, unconditionally overwriting any same-named module
# already cached from another plugin.
_load_by_path("bootstrap", _TOOLS / "bootstrap.py")

import bootstrap as _vpd_bootstrap  # noqa: E402
_vpd_bootstrap.bootstrap()
