#!/usr/bin/env python3
"""Plugin bootstrap lifecycle registry for the framework verification platform.

Responsibility boundary
-----------------------
This module owns the **bootstrap lifecycle** of verification plugins.  It
does NOT perform backend registration itself — that is owned by each plugin's
``bootstrap()`` function, which delegates to
``forge.verification.backend_registry.register_backend()``.

Lifecycle states
-----------------
A plugin moves through the following states relative to the framework:

  1. **Undeclared** — the plugin has never called ``declare_plugin_bootstrap()``.
     ``bootstrap_plugin()`` will fail with a ``LookupError``.

  2. **Declared** — the plugin's ``bootstrap.py`` (or equivalent) has been
     imported and called ``declare_plugin_bootstrap()``.  The framework knows
     *how* to bootstrap the plugin but has not yet done so.

  3. **Bootstrapped** — ``bootstrap()`` has been called (either directly or
     via ``bootstrap_plugin()``).  Backend registration, schema extensions, and
     any other one-time setup are complete.  Subsequent calls are no-ops.

Public API
----------
  declare_plugin_bootstrap(plugin_id, module_name)  — called by plugin's
      bootstrap.py at module level to register the canonical entry point
  is_plugin_bootstrapped(plugin_id) → bool
  bootstrap_plugin(plugin_id)       — idempotent; imports module + calls bootstrap()
  bootstrap_from_flow(flow_path)    — convenience: parses flow.plugin + bootstraps
  require_plugin_bootstrapped(plugin_id) — raise RuntimeError if not bootstrapped
  mark_bootstrapped(plugin_id, *, backends, flow_kinds) — called by plugin bootstrap()
  list_registered_plugins() → list[str]
  get_plugin_capabilities(plugin_id) → dict | None
  introspect() → dict

Test utilities
--------------
  _reset_for_testing() — clears all bootstrap and backend registry state;
      use in fixtures that need to test bootstrap failure paths.

Bootstrap contract for plugin authors
--------------------------------------
Every plugin that integrates with the framework verification platform must
provide a ``bootstrap.py`` (or equivalent entry module) that:

1. Declares itself at module level::

       from forge.verification.plugin_registry import declare_plugin_bootstrap
       declare_plugin_bootstrap(PLUGIN_ID, __name__)

2. Exposes a ``bootstrap()`` function that is idempotent::

       def bootstrap() -> None:
           from forge.verification.plugin_registry import is_plugin_bootstrapped, mark_bootstrapped
           if is_plugin_bootstrapped(PLUGIN_ID):
               return
           # ... register backends, etc. ...
           mark_bootstrapped(PLUGIN_ID, backends=[...], flow_kinds=[...])

3. The bootstrap entry point is imported and called explicitly by:
   - CLI entry points before ``load_flow()``
   - Test conftest before any test module
   - Framework orchestrators before backend dispatch

Plugin bootstrap must NOT be triggered by importing unrelated utility modules.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


# ── Internal state ─────────────────────────────────────────────────────────

# plugin_id → importable module name (set by declare_plugin_bootstrap)
_PLUGIN_BOOTSTRAP_MODULES: dict[str, str] = {}

# plugin_id → capability dict (set by mark_bootstrapped)
_PLUGIN_CAPABILITIES: dict[str, dict[str, Any]] = {}

# Set of plugin IDs whose bootstrap() has completed successfully
_BOOTSTRAPPED: set[str] = set()


# ── Declaration API ────────────────────────────────────────────────────────

def declare_plugin_bootstrap(plugin_id: str, module_name: str) -> None:
    """Register the importable bootstrap module for *plugin_id*.

    Call this at module level inside the plugin's bootstrap entry point so
    the framework can find and invoke it via ``bootstrap_plugin()``::

        from forge.verification.plugin_registry import declare_plugin_bootstrap
        declare_plugin_bootstrap("myplugin", __name__)

    Args:
        plugin_id:   Short identifier, e.g. ``"my_plugin"``.
        module_name: Importable module name that can be passed to
                     ``importlib.import_module()``.  The module must be on
                     ``sys.path`` when ``bootstrap_plugin()`` is called.
    """
    _PLUGIN_BOOTSTRAP_MODULES[plugin_id] = module_name


# ── Query API ──────────────────────────────────────────────────────────────

def is_plugin_bootstrapped(plugin_id: str) -> bool:
    """Return True if *plugin_id* has completed bootstrap."""
    return plugin_id in _BOOTSTRAPPED


def get_plugin_capabilities(plugin_id: str) -> dict[str, Any] | None:
    """Return the capability dict recorded during bootstrap, or None.

    Returns None if the plugin has not been bootstrapped or declared no
    capabilities.
    """
    return _PLUGIN_CAPABILITIES.get(plugin_id)


def list_registered_plugins() -> list[str]:
    """Return a sorted list of plugin IDs that have been bootstrapped."""
    return sorted(_BOOTSTRAPPED)


# ── Guard API ──────────────────────────────────────────────────────────────

def require_plugin_bootstrapped(plugin_id: str) -> None:
    """Raise ``RuntimeError`` if *plugin_id* has not been bootstrapped.

    Called by framework components (e.g. ``flow_loader``) at the first
    operation that requires a plugin to be ready, so errors surface with a
    clear diagnostic rather than a confusing ``ValueError`` about an unknown
    backend.

    Example message::

        RuntimeError: Plugin 'my_plugin' has not been bootstrapped.
        Import and call its bootstrap entry point before loading flows:

            import bootstrap
            bootstrap.bootstrap()

        Or use bootstrap_plugin('my_plugin') if the plugin has been declared.
    """
    if plugin_id not in _BOOTSTRAPPED:
        _known = (
            f"Use bootstrap_plugin({plugin_id!r}) if the plugin has been declared."
            if plugin_id in _PLUGIN_BOOTSTRAP_MODULES
            else f"Import the plugin's bootstrap.py and call bootstrap.bootstrap()."
        )
        raise RuntimeError(
            f"Plugin {plugin_id!r} has not been bootstrapped.\n"
            f"Import and call its bootstrap entry point before loading flows:\n\n"
            f"    import bootstrap\n"
            f"    bootstrap.bootstrap()\n\n"
            f"{_known}"
        )


# ── Mark API (called by plugin bootstrap()) ────────────────────────────────

def mark_bootstrapped(
    plugin_id: str,
    *,
    backends: list[str] | None = None,
    flow_kinds: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Record that *plugin_id* has completed bootstrap.

    Called at the **end** of a plugin's ``bootstrap()`` function.  Idempotent
    — calling it multiple times with the same ``plugin_id`` is harmless (later
    calls update the capability record).

    Args:
        plugin_id:  Plugin identifier.
        backends:   List of backend IDs this plugin registered.
        flow_kinds: List of flow kinds this plugin supports.
        extra:      Any additional plugin-specific metadata.
    """
    _BOOTSTRAPPED.add(plugin_id)
    caps: dict[str, Any] = {}
    if backends is not None:
        caps["backends"] = sorted(backends)
    if flow_kinds is not None:
        caps["flow_kinds"] = sorted(flow_kinds)
    if extra:
        caps.update(extra)
    _PLUGIN_CAPABILITIES[plugin_id] = caps


# ── Bootstrap execution API ────────────────────────────────────────────────

def bootstrap_plugin(plugin_id: str) -> None:
    """Bootstrap *plugin_id* by importing its declared module and calling bootstrap().

    Idempotent — if the plugin is already bootstrapped this is a no-op.

    The plugin must have called ``declare_plugin_bootstrap(plugin_id, module_name)``
    before this function is called, otherwise a ``LookupError`` is raised.

    Raises:
        LookupError  — plugin_id has not been declared via
                       ``declare_plugin_bootstrap()``.
        ImportError  — the declared bootstrap module cannot be imported.
        AttributeError — the module does not expose a ``bootstrap()`` function.
    """
    if is_plugin_bootstrapped(plugin_id):
        return
    if plugin_id not in _PLUGIN_BOOTSTRAP_MODULES:
        raise LookupError(
            f"Plugin {plugin_id!r} has not been declared.  "
            f"Import its bootstrap.py first so it can call "
            f"declare_plugin_bootstrap().\n"
            f"Known plugins: {sorted(_PLUGIN_BOOTSTRAP_MODULES) or '(none)'}"
        )
    mod_name = _PLUGIN_BOOTSTRAP_MODULES[plugin_id]
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as exc:
        raise ImportError(
            f"Bootstrap module {mod_name!r} for plugin {plugin_id!r} "
            f"could not be imported: {exc}"
        ) from exc
    if not hasattr(mod, "bootstrap"):
        raise AttributeError(
            f"Bootstrap module {mod_name!r} does not expose a 'bootstrap()' function."
        )
    mod.bootstrap()


def bootstrap_from_flow(flow_path: Path) -> None:
    """Parse *flow_path*, extract ``flow.plugin``, and bootstrap that plugin.

    Convenience helper for launchers and orchestrators that derive the
    required plugin from the flow file itself.  Idempotent if the plugin is
    already bootstrapped.

    The plugin identified by ``flow.plugin`` must have already called
    ``declare_plugin_bootstrap()`` (i.e. its bootstrap module must have been
    imported, even if ``bootstrap()`` has not yet been called).

    If the flow file does not declare a ``flow.plugin`` field this function
    is a no-op.

    Raises:
        FileNotFoundError — flow_path does not exist.
        RuntimeError      — PyYAML not installed.
        LookupError       — plugin_id not declared.
    """
    if not Path(flow_path).exists():
        raise FileNotFoundError(f"Flow file not found: {flow_path}")
    try:
        import yaml as _yaml
    except ImportError:
        raise RuntimeError("PyYAML is required: pip install pyyaml")

    raw = _yaml.safe_load(Path(flow_path).read_text()) or {}
    plugin_id = (raw.get("flow") or {}).get("plugin")
    if plugin_id:
        bootstrap_plugin(str(plugin_id))


# ── Introspection ──────────────────────────────────────────────────────────

def introspect() -> dict[str, Any]:
    """Return a snapshot of the current bootstrap state.

    Returned structure::

        {
                    "bootstrapped_plugins": ["my_plugin", ...],
                    "declared_plugins":     ["my_plugin", ...],
          "plugins": {
                        "my_plugin": {
              "bootstrapped": True,
              "declared":     True,
              "bootstrap_module": "bootstrap",
              "capabilities": {
                "backends":    ["cosim", "csim", "verilator", "xsim"],
                "flow_kinds":  ["full_chip_rtl", ...],
              },
            },
          },
        }
    """
    all_ids = sorted(set(_PLUGIN_BOOTSTRAP_MODULES) | _BOOTSTRAPPED)
    plugins = {}
    for pid in all_ids:
        plugins[pid] = {
            "bootstrapped":       pid in _BOOTSTRAPPED,
            "declared":           pid in _PLUGIN_BOOTSTRAP_MODULES,
            "bootstrap_module":   _PLUGIN_BOOTSTRAP_MODULES.get(pid),
            "capabilities":       _PLUGIN_CAPABILITIES.get(pid, {}),
        }
    return {
        "bootstrapped_plugins": sorted(_BOOTSTRAPPED),
        "declared_plugins":     sorted(_PLUGIN_BOOTSTRAP_MODULES),
        "plugins":              plugins,
    }


# ── Test utilities ─────────────────────────────────────────────────────────

def _reset_for_testing() -> None:
    """Clear all bootstrap state and backend registry state.

    FOR TESTING ONLY.  Allows test functions to verify bootstrap failure paths.
    After calling this function, ``is_plugin_bootstrapped()`` returns False for
    all plugins and ``get_allowed_backends()`` returns an empty frozenset.

    The plugin declaration table (``_PLUGIN_BOOTSTRAP_MODULES``) is intentionally
    preserved — modules are only imported once per process, so re-declaration
    cannot happen once reset.  ``bootstrap_plugin()`` remains callable after
    reset as long as the bootstrap module was previously declared.

    Callers should restore bootstrap state in their fixture teardown::

        @pytest.fixture
        def clean_bootstrap():
            pr._reset_for_testing()
            yield
            pr._reset_for_testing()
            import bootstrap; bootstrap.bootstrap()
    """
    _BOOTSTRAPPED.clear()
    _PLUGIN_CAPABILITIES.clear()
    # Also clear backend registry so it returns [] after reset
    import forge.verification.backend_registry as _br
    _br._BACKEND_MODULE_NAMES.clear()
    _br.get_compatible_pairs.cache_clear()
