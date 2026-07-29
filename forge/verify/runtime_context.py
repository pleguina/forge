#!/usr/bin/env python3
"""Generic runtime execution context for backend adapters.

Responsibility boundary
-----------------------
This module defines the **framework-generic** portion of the runtime context
that is passed to every backend adapter method.

Framework code constructs this object (or a plugin-supplied subclass) from
resolved overrides and passes it to every backend adapter method.  Backend
adapters receive only resolved values and must not reconstruct flow state
themselves.

Generic fields
--------------
  work_dir         Absolute path to the backend's working directory.
  probe_log        Whether Tier 2 probe CSV capture is enabled.
  event_index      Internal, always-numeric event position for
                    ``stimulus_mode: readmemh`` flows (slice 7.5) — the
                    ``+EVENT_INDEX=N`` plusarg backends pass to the
                    simulator. ``None`` for the default ``svh_include``
                    mechanism, which needs no runtime event selection.
  extra_overrides  Arbitrary backend-specific overrides (backend-prefixed
                   keys, e.g. ``{"xsim_debug": True}``).

Plugin extension
----------------
Plugins subclass this to add plugin-specific execution state::

    @dataclass(kw_only=True)
    class PluginRuntimeContext(RuntimeContext):
        event_id:  int   # plugin-specific event index to simulate
        dataset:   Path  # plugin-specific stimulus or dataset path

Plugin adapters type their ``ctx`` parameter against the plugin-specific
subclass.  Framework code only ever accesses the generic fields declared here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeContext:
    """Generic runtime state passed to backend adapter methods.

    Constructed by the framework launcher after resolving all overrides.
    Backend adapters must not need to read environment variables or
    reconstruct generic flow state from scratch.

    Plugin-specific execution state (e.g. event index, stimulus file path)
    belongs in a plugin-provided subclass, not in this generic base.
    """

    work_dir:  "Any"  # Path — typed as Any to avoid importing Path from pathlib
                      # at module level for callers that defer path resolution.
                      # Always pass a Path object in practice.

    probe_log: bool = False  # enable Tier 2 probe CSV capture

    event_index: "int | None" = None  # readmemh-mode runtime event selection (slice 7.5)

    # Backend-specific overrides use backend-prefixed keys, e.g.:
    #   {"xsim_debug": True, "xsim_extra_flags": "--debug all"}
    extra_overrides: dict[str, Any] = field(default_factory=dict)
