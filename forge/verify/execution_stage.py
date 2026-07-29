#!/usr/bin/env python3
"""Execution-stage enum shared by all backend adapters.

Replaces free-form ``backend_metadata["step"]`` strings as the mechanism
for recording *which stage of a simulation run* produced a given
:class:`~forge.verify.backend_base.ExecutionResult`.  Downstream consumers
(log selection, diagnostics) branch on this enum instead of deriving stage
identity from a hand-written string.
"""
from __future__ import annotations

from enum import Enum


class ExecutionStage(str, Enum):
    """A stage in a backend's execution pipeline.

    Not every backend uses every stage — e.g. Verilator has no separate
    ``ELABORATE`` step (compile and elaborate are one invocation), so it
    only ever reports ``COMPILE``/``SIMULATE``.  ``PREFLIGHT`` is reserved
    for failures caught before any tool invocation (missing artifacts,
    missing tools). ``RESULT_PARSE`` is reserved for failures while
    interpreting a backend's own output after a successful run.
    """

    PREFLIGHT    = "preflight"
    COMPILE      = "compile"
    ELABORATE    = "elaborate"
    SIMULATE     = "simulate"
    POST_CHECK   = "post_check"
    RESULT_PARSE = "result_parse"
