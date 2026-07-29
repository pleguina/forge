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
"""

from __future__ import annotations

import sys

import pytest

_PLUGIN_TOOL_MODULE_NAMES = ("bootstrap", "gen_stimulus", "flow_config", "runtime_context")


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


@pytest.fixture(autouse=True)
def _isolate_plugin_bootstrap_state():
    _clear_plugin_bootstrap_state()
    yield
    _clear_plugin_bootstrap_state()
