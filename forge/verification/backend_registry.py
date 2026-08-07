#!/usr/bin/env python3
"""Backend registry and dispatcher for the framework verification platform.

Responsibility boundary
-----------------------
This module is the **single authority** for mapping ``flow.backend`` values
to their adapter implementations.

Once a plugin has registered its backends, no other framework code needs
``if backend == "xsim"`` conditionals.  All backend-specific dispatch goes
through this module.

Public API
----------
  register_backend(backend_id, module_name)     — plugin registration call
  get_adapter(backend_id)                       → BackendAdapter
  list_supported_backends()                     → list[str]
  validate_artifact_requirements(adapter, cfg)  → list[str]
  get_allowed_backends()                        → frozenset[str]
  get_compatible_pairs()                        → frozenset[tuple[str, str]]
  introspect()                                  → dict

Plugin integration
------------------
Each plugin imports this module and registers its backend adapters before
flow loading begins::

    from forge.verification.backend_registry import register_backend
    register_backend("xsim",      "backend_xsim")
    register_backend("verilator", "backend_verilator")

Backend modules must be importable at call time (i.e. their parent directory
must already be on sys.path when register_backend is called or when
get_adapter is first called for a given backend).

Rules
-----
* Unsupported backend identifiers raise ``ValueError`` at call time.
* Adapter modules that are missing or malformed raise ``ImportError`` /
  ``AttributeError`` / ``TypeError`` with clear diagnostic messages.
* The registry is intentionally empty by default; plugins populate it.
"""
from __future__ import annotations

import functools
import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from forge.verification.backend_base import BackendAdapter

if TYPE_CHECKING:
    pass  # FlowConfig is plugin-supplied; typed as Any in BackendAdapter


# ── Registry table ─────────────────────────────────────────────────────────
# Maps flow.backend value → importable module name.
# Populated at runtime by register_backend() calls from plugin shims.
# Empty by default — no backends are known until a plugin registers them.

_BACKEND_MODULE_NAMES: dict[str, str] = {}


# ── Registration API ───────────────────────────────────────────────────────

def register_backend(backend_id: str, module_name: str) -> None:
    """Register a backend adapter module with the registry.

    Must be called before any ``get_adapter`` or ``get_compatible_pairs``
    call that references this backend.

    Args:
        backend_id:   The string used in ``flow.backend`` (e.g. ``"xsim"``).
        module_name:  Importable module name (e.g. ``"backend_xsim"``).
                      The module must be importable when ``get_adapter`` is
                      called — the caller is responsible for ensuring its
                      directory is on ``sys.path``.
    """
    _BACKEND_MODULE_NAMES[backend_id] = module_name
    # Invalidate the lru_cache since the registry changed.
    get_compatible_pairs.cache_clear()


# ── Public API ─────────────────────────────────────────────────────────────

def list_supported_backends() -> list[str]:
    """Return the sorted list of backend identifiers known to the registry."""
    return sorted(_BACKEND_MODULE_NAMES)


def get_adapter(backend_id: str) -> BackendAdapter:
    """Return the backend adapter for *backend_id*.

    Raises
    ------
    ValueError      — backend_id not in the registry table.
    ImportError     — adapter module cannot be imported.
    AttributeError  — adapter module does not export an ``ADAPTER`` symbol.
    TypeError       — ``ADAPTER`` is not a ``BackendAdapter`` instance.
    """
    if backend_id not in _BACKEND_MODULE_NAMES:
        supported = ", ".join(sorted(_BACKEND_MODULE_NAMES)) or "(none registered)"
        raise ValueError(
            f"Unsupported backend: {backend_id!r}.  "
            f"Supported backends: {supported}"
        )

    mod_name = _BACKEND_MODULE_NAMES[backend_id]

    try:
        mod = importlib.import_module(mod_name)
    except ImportError as exc:
        raise ImportError(
            f"Backend module {mod_name!r} for backend {backend_id!r} "
            f"could not be imported: {exc}"
        ) from exc

    if not hasattr(mod, "ADAPTER"):
        raise AttributeError(
            f"Backend module {mod_name!r} does not export an 'ADAPTER' symbol.  "
            f"Every backend module must define: ADAPTER = <BackendAdapter subclass>()"
        )

    adapter = mod.ADAPTER
    if not isinstance(adapter, BackendAdapter):
        raise TypeError(
            f"Backend module {mod_name!r}: ADAPTER is {type(adapter)!r}, "
            f"expected a BackendAdapter instance."
        )

    return adapter


def validate_artifact_requirements(
    adapter: BackendAdapter,
    cfg: object,
) -> list[str]:
    """Check that all non-optional required artifacts declared by *adapter* exist.

    Iterates ``adapter.required_artifacts``.  For each requirement with a
    ``path_attr``, looks up the attribute on *cfg* and checks if the path
    exists on disk.

    Returns a list of human-readable error strings.  Empty list = all OK.

    Note: requirements with ``path_attr=None`` are skipped — those must be
    validated by ``adapter.validate_backend_requirements(cfg)`` using
    backend-specific custom logic.
    """
    errors: list[str] = []
    for req in adapter.required_artifacts:
        if req.path_attr is None:
            continue  # custom validation: handled in adapter.validate_backend_requirements
        path: Path | None = getattr(cfg, req.path_attr, None)
        if path is None:
            if not req.optional:
                errors.append(
                    f"Required artifact {req.name!r}: path not set in flow config"
                )
        elif not path.exists():
            if not req.optional:
                errors.append(
                    f"Required artifact {req.name!r} not found: {path}"
                )
    return errors


# ── Introspection API ──────────────────────────────────────────────────────

def get_allowed_backends() -> frozenset[str]:
    """Return the frozenset of backend identifiers registered in this registry.

    This is the single authoritative source for which backend strings are
    valid.  Flow loaders derive their backend-allowed check from this function
    instead of maintaining a shadow constant.
    """
    return frozenset(_BACKEND_MODULE_NAMES)


@functools.lru_cache(maxsize=None)
def get_compatible_pairs() -> frozenset[tuple[str, str]]:
    """Return the (flow_kind, backend_id) compatibility matrix.

    Built by loading every registered adapter and unioning their
    ``compatible_flow_kinds`` sets.  The result is cached after the first
    call; adapter ``compatible_flow_kinds`` sets are immutable so the cache
    is safe across the lifetime of the process.

    Adapters whose modules cannot be imported are silently skipped — they
    contribute no pairs.  This allows the registry to list a backend before
    its implementation module is fully available.

    The cache is automatically invalidated when ``register_backend`` is
    called so late registrations are always reflected.
    """
    pairs: set[tuple[str, str]] = set()
    for backend_id in _BACKEND_MODULE_NAMES:
        try:
            adapter = get_adapter(backend_id)
            for kind in adapter.compatible_flow_kinds:
                pairs.add((kind, backend_id))
        except (ImportError, AttributeError, TypeError):
            pass  # skip unavailable / not-yet-implemented adapters
    return frozenset(pairs)


def introspect() -> dict:
    """Return a machine-readable snapshot of all declared adapter contracts.

    Returned structure::

        {
          "backends": {
            "<id>": {
              "status":                "available" | "unavailable",
              "compatible_flow_kinds": [...],
              "required_tools":        [...],
              "capabilities": {
                "supports_waveform":          bool,
                "supports_probe_log":         bool,
                "requires_vendor_env":        bool,
                "supports_checker_post_pass": bool,
              },
              "required_artifacts": [
                {"name": str, "path_attr": str | None, "optional": bool},
                ...
              ],
            },
            # or, if unavailable:
            "<id>": {"status": "unavailable", "error": "<reason>"},
          },
          "compatibility_matrix": [
            {"kind": str, "backend": str},
            ...
          ],
        }
    """
    backends: dict = {}
    for backend_id in sorted(_BACKEND_MODULE_NAMES):
        try:
            adapter = get_adapter(backend_id)
            cap = adapter.capabilities
            backends[backend_id] = {
                "status": "available",
                "compatible_flow_kinds": sorted(adapter.compatible_flow_kinds),
                "required_tools": list(adapter.required_tools),
                "capabilities": {
                    "supports_waveform":          cap.supports_waveform,
                    "supports_probe_log":          cap.supports_probe_log,
                    "requires_vendor_env":         cap.requires_vendor_env,
                    "supports_checker_post_pass":  cap.supports_checker_post_pass,
                },
                "required_artifacts": [
                    {
                        "name":      r.name,
                        "path_attr": r.path_attr,
                        "optional":  r.optional,
                    }
                    for r in adapter.required_artifacts
                ],
            }
        except (ImportError, AttributeError, TypeError) as exc:
            backends[backend_id] = {"status": "unavailable", "error": str(exc)}

    compatibility_matrix = sorted(
        ({"kind": kind, "backend": bid}
         for bid, info in backends.items()
         if info.get("status") == "available"
         for kind in info["compatible_flow_kinds"]),
        key=lambda e: (e["kind"], e["backend"]),
    )

    return {
        "backends": backends,
        "compatibility_matrix": compatibility_matrix,
    }
