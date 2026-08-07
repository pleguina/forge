#!/usr/bin/env python3
"""Abstract base class and shared dataclasses for all backend adapters.

Responsibility boundary
-----------------------
This module defines the **enforceable contract** for every backend adapter.

Every backend adapter module must:
  1. Define a class that inherits from ``BackendAdapter``.
  2. Implement all abstract properties and methods declared below.
  3. Export a module-level ``ADAPTER`` singleton of that class.

The registry (``forge.verification.backend_registry``) imports ``ADAPTER`` from each
backend module and dispatches calls through this interface.  Framework code
must rely only on this contract — never on backend-specific file names,
command strings, or implementation details.

Shared contracts
----------------
  BackendCapabilities   — static feature flags per backend
  ArtifactRequirement   — declaration of one required pre-execution artifact
  ExecutionResult       — normalized result returned by run_backend()
  BackendAdapter        — abstract base class all adapters must implement

Execution interface (in call order)
------------------------------------
  1. validate_backend_requirements(cfg)   → list[str]   (empty = OK)
  2. prepare_backend_inputs(cfg, ctx)     → None
  3. run_backend(cfg, ctx)                → ExecutionResult
  4. describe_backend_outputs(cfg, ctx)   → dict[str, Path | None]

Type annotations
----------------
The ``cfg`` parameter is typed as ``Any`` in this module.  Plugin adapters
should narrow the type to their specific FlowConfig subclass in their own
method signatures.  The framework places no constraints on the flow config
type beyond runtime duck-typing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.verification.execution_stage import ExecutionStage


# ── Shared data contracts ──────────────────────────────────────────────────

@dataclass(frozen=True)
class BackendCapabilities:
    """Static feature flags declared by a backend adapter.

    Framework code queries these flags instead of using backend-specific
    conditionals.  All fields are required — adapters must be explicit about
    every capability.
    """
    supports_waveform:          bool  # can produce a simulation waveform file
    supports_probe_log:         bool  # can produce a Tier 2 probe CSV
    requires_vendor_env:        bool  # needs a vendor toolchain env (e.g. XILINX_VIVADO)
    supports_checker_post_pass: bool  # produces output consumable by a checker


@dataclass(frozen=True)
class ArtifactRequirement:
    """Declaration of one artifact required by a backend before execution.

    path_attr:  Attribute name on the flow config that yields the artifact
                ``Path``.  ``None`` for artifacts the adapter validates via
                custom logic in ``validate_backend_requirements``.
    optional:   If ``True`` a missing artifact is a warning, not an error.
    """
    name:      str
    path_attr: str | None = None
    optional:  bool = False


@dataclass
class ExecutionResult:
    """Normalized result returned by ``BackendAdapter.run_backend()``.

    The common fields below are stable.  Backends add backend-specific data
    to ``backend_metadata``.  Generic post-processing (summary reporting,
    checker invocation) reads only the common fields.
    """
    success:              bool
    waveform_path:        Path | None = None
    log_path:             Path | None = None
    observed_output_path: Path | None = None  # CSV fed to the checker
    exit_code:            int = 0
    backend_metadata:     dict[str, Any] = field(default_factory=dict)
    stage:                ExecutionStage | None = None  # which stage produced this result
    duration_s:           float | None = None  # wall-clock seconds for this stage's subprocess
    backend_id:           str = ""  # e.g. "xsim", "verilator" — set by the adapter itself


# ── Abstract base class ────────────────────────────────────────────────────

class BackendAdapter(ABC):
    """Abstract base class for all simulator backend adapters.

    Required interface
    ------------------
    Every concrete backend must implement all abstract members below.
    The framework dispatcher (``forge.verification.backend_registry``) calls these
    methods and must never need backend-specific conditionals beyond adapter
    lookup.

    Abstract properties
    -------------------
    backend_id              str
    compatible_flow_kinds   frozenset[str]
    required_tools          tuple[str, ...]
    capabilities            BackendCapabilities
    required_artifacts      tuple[ArtifactRequirement, ...]

    Abstract methods (called in this order per run)
    -----------------------------------------------
    validate_backend_requirements(cfg)   → list[str]
    prepare_backend_inputs(cfg, ctx)     → None
    run_backend(cfg, ctx)                → ExecutionResult
    describe_backend_outputs(cfg, ctx)   → dict[str, Path | None]
    """

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Unique backend identifier matching ``flow.backend`` in the flow file."""

    @property
    @abstractmethod
    def compatible_flow_kinds(self) -> frozenset[str]:
        """Set of flow kinds this backend can execute."""

    @property
    @abstractmethod
    def required_tools(self) -> tuple[str, ...]:
        """Command names that must be on PATH before any execution step."""

    # ── Capability and artifact metadata ──────────────────────────────────

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Static feature flags for this backend."""

    @property
    @abstractmethod
    def required_artifacts(self) -> tuple[ArtifactRequirement, ...]:
        """Artifacts the adapter requires before execution.

        Used by ``forge.verification.backend_registry.validate_artifact_requirements()``
        for early failure detection.  Adapters may also check artifacts inside
        ``validate_backend_requirements()`` for custom logic.
        """

    # ── Execution interface ───────────────────────────────────────────────

    @abstractmethod
    def validate_backend_requirements(self, cfg: Any) -> list[str]:
        """Check backend-specific prerequisites (toolchain, environment).

        Returns a list of human-readable error strings.  An empty list means
        all prerequisites are satisfied.

        This method must NOT launch any tool or simulator.  Artifact existence
        is checked separately via
        ``forge.verification.backend_registry.validate_artifact_requirements()``.
        """

    @abstractmethod
    def prepare_backend_inputs(self, cfg: Any, ctx: Any) -> None:
        """Prepare all backend-specific inputs before simulation.

        Examples: generate ``compile.prj``, stage ``.dat`` files, write a
        Makefile.  Must be idempotent (safe to call again on the same work_dir).
        """

    @abstractmethod
    def run_backend(self, cfg: Any, ctx: Any) -> ExecutionResult:
        """Execute the simulation and return a normalized result."""

    @abstractmethod
    def describe_backend_outputs(
        self,
        cfg: Any,
        ctx: Any,
    ) -> dict[str, Path | None]:
        """Return ``{output_name: path}`` for all outputs this backend may produce.

        Paths may not exist yet — this is a declaration of expected outputs, not
        an existence check.  Generic reporting consumes this map.
        """
