"""Framework verification Python platform.

Provides generic verification machinery that every plugin consuming the
framework can rely on without rebuilding it.

Modules
-------
  fw_verify.backend_base      BackendAdapter ABC and shared contract types
  fw_verify.backend_registry  backend discovery, dispatch, and introspection
  fw_verify.backend_xsim      Generic Xilinx XSIM backend (framework-owned)
  fw_verify.backend_csim      Generic HLS CSIM backend   (framework-owned)
  fw_verify.flow_loader       generic flow schema definitions and building blocks
  fw_verify.plugin_registry   plugin bootstrap lifecycle and capability tracking
  fw_verify.preflight         artifact existence and consistency checks
  fw_verify.runtime_context   generic runtime execution context model

Plugin integration pattern
--------------------------
A plugin connects to the framework through one explicit bootstrap entry point:

  1. Create ``bootstrap.py`` that calls ``declare_plugin_bootstrap()`` at
     module level and exposes a ``bootstrap()`` function.
  2. ``bootstrap()`` MAY call ``register_backend()`` for custom backends.
     For xsim and csim, framework-owned defaults are auto-registered here
     and no plugin registration is needed for the standard case.
  3. Launchers, CLIs, and test conftest files import ``bootstrap`` and call
     ``bootstrap.bootstrap()`` before any flow loading or backend dispatch.
  4. Flow files declare ``flow.plugin: <plugin_id>``; the framework checks
     that the plugin is bootstrapped before proceeding.

Plugin authors subclass:
  - ``BackendAdapter`` — one subclass per simulator backend (only for overrides)
  - ``RuntimeContext``  — adds plugin-specific execution state
  - ``FlowConfig``      — adds plugin-specific schema fields

Plugin authors do NOT rebuild the registry, loader, preflight, adapter
contract, or bootstrap mechanics — they declare only the plugin-specific pieces.
All generic orchestration is owned by this package.

Framework-owned backends (auto-registered)
-------------------------------------------
  xsim  →  fw_verify.backend_xsim.XsimBackend
  csim  →  fw_verify.backend_csim.CsimBackend

Plugins can override these by calling ``register_backend()`` after import.
"""

# Auto-register framework-owned backends so plugins do not need to declare them.
# Import is deferred to avoid circular imports at module level.
def _register_framework_backends() -> None:
    from arc.verify.backend_registry import register_backend
    register_backend("xsim", "fw_verify.backend_xsim")
    register_backend("csim", "fw_verify.backend_csim")


_register_framework_backends()
del _register_framework_backends

