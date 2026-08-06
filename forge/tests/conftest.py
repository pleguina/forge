"""Shared pytest fixtures.

Plugin bootstrap (``forge/verify/plugin_registry.py``) imports each
plugin's ``tools/bootstrap.py``/``gen_stimulus.py``/``flow_config.py``/
``runtime_context.py`` via bare, non-namespaced module names, after
inserting that plugin's own ``tools/`` directory onto ``sys.path``.
Python caches modules by name in ``sys.modules``, so two different tests
that each copy a plugin (or scaffold a fresh one) into their own
``tmp_path`` — ``tests/verify/test_verify_run_xsim.py``,
``tests/test_test_cli_group.py``, ``tests/test_report_cli_group.py``,
``tests/test_init_cli_group.py`` all do this — can otherwise get each
other's *previously cached* module instead of their own tmp_path's file
(a real cross-test contamination bug found while adding the newer three
of those files: a later test would silently run an earlier test's
plugin's ``gen_stimulus.py``, generating the wrong DUT signal names).
This autouse fixture clears both the module cache and the plugin
registry's bootstrap-declaration state around every test, so each test's
tmp_path-scoped plugin tooling is always loaded fresh.

``sys.path`` itself needs the same treatment: ``forge/verify/__main__.py``'s ``_bootstrap()`` does
``sys.path.insert(0, tools_dir)`` per test but this fixture never undid
it, so a real plugin's real (not tmp_path-copied) ``tools/`` directory —
e.g. ``plugins/vision_pipeline_demo/forge/verify/tools`` — stays in
``sys.path`` forever once any test bootstraps it. A later test that
bootstraps a *different* real plugin inserts its own ``tools/`` dir
*ahead* of the earlier one, which is harmless on its own — but once a
*third* test clears ``sys.modules["bootstrap"]`` (via this very fixture)
and tries to re-bootstrap the *first* plugin again, Python's import
search walks ``sys.path`` in order and can resolve the bare ``bootstrap``
name to whichever plugin's ``tools/`` dir now sits first, silently
bootstrapping the wrong plugin instead of raising ImportError — found for
real running ``test_report_cli_group.py`` and
``test_golden_comparison_integration.py`` (both bootstrap
``vision_pipeline_demo`` for real, not from a tmp_path copy) in the same
session as any test that bootstraps another real plugin in between.
Snapshotting and restoring ``sys.path`` around every test closes this the
same way the module-cache clearing already does.
"""

from __future__ import annotations

import sys

import pytest

_PLUGIN_TOOL_MODULE_NAMES = (
    "bootstrap", "gen_stimulus", "flow_config", "runtime_context", "dataset_adapter",
)


def _clear_plugin_bootstrap_state() -> None:
    for name in _PLUGIN_TOOL_MODULE_NAMES:
        sys.modules.pop(name, None)
    try:
        import forge.verify.plugin_registry as _pr
    except ImportError:
        return
    _pr._reset_for_testing()
    _pr._PLUGIN_BOOTSTRAP_MODULES.clear()

    # _reset_for_testing() also clears the backend registry
    # (forge.verify.backend_registry._BACKEND_MODULE_NAMES) — including the
    # framework-owned xsim/csim defaults that forge/verify/__init__.py only
    # registers once, at first import. Since that import already happened
    # earlier in the session, it never re-runs — re-register those defaults
    # here exactly as forge/verify/__init__.py does, or every xsim/csim flow
    # in every later test would fail with "flow.backend must be one of ()".
    from forge.verify.backend_registry import register_backend

    register_backend("xsim", "forge.verify.backend_xsim")
    register_backend("csim", "forge.verify.backend_csim")
    register_backend("verilator", "forge.verify.backend_verilator")


@pytest.fixture(autouse=True)
def _isolate_plugin_bootstrap_state():
    original_sys_path = list(sys.path)
    _clear_plugin_bootstrap_state()
    yield
    sys.path[:] = original_sys_path
    _clear_plugin_bootstrap_state()
