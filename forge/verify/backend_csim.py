#!/usr/bin/env python3
"""Generic HLS CSIM backend — framework-owned.

A plugin author does **not** need to write ``backend_csim.py``.
This module is the default csim backend for all plugins that run
pre-compiled HLS C-simulation testbenches without plugin-specific overrides.

Responsibility boundary
-----------------------
This module owns the generic CSIM execution lifecycle:

  * Testbench binary discovery (build dirs, env var, PATH)
  * Tool-availability validation
  * Binary execution and log capture
  * Pass/fail classification (exit code + log scan)
  * Artifact reporting

Plugin-specific concerns NOT owned here
----------------------------------------
  * What the testbench binary checks                → plugin-owned C++
  * How the binary is compiled / which HLS project  → plugin HLS build
    * Custom CLI argument conventions beyond a common contract (see below)
    * Plugin-specific extended CLI options
    → overridden by the plugin-local backend_csim.py if needed

Binary discovery order
-----------------------
1. ``CSIM_TB_<TB_NAME_UPPER>`` environment variable (explicit override)
2. ``<consumer_root>/build_targeted/<tb_name>``
3. ``<consumer_root>/build/<tb_name>``
4. ``PATH`` (shutil.which)

Standard testbench CLI contract
---------------------------------
The framework invokes the testbench as::

    <tb_binary> <dataset_xml>

For v1.0 verification, ``dataset_xml`` is the framework-standard XML-backed
dataset input.

Returns 0 on PASS, non-zero on FAIL.  Stdout/stderr are captured to
``simulate.log``.

If your testbench uses a different CLI, ship a plugin-local ``backend_csim.py``
that subclasses ``CsimBackend`` and overrides only ``run_backend()``.

Override policy
---------------
A plugin may still ship ``backend_csim.py`` with a custom ``ADAPTER``.
When the plugin registers it via ``register_backend("csim", "backend_csim")``,
that takes precedence over the framework default.

Auto-registration
-----------------
Auto-registered at ``forge verify`` package import time.  No plugin
``bootstrap.py`` needs to call ``register_backend("csim", ...)``.

ADAPTER singleton
-----------------
``ADAPTER = CsimBackend()``
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from forge.verify.backend_base import (
    ArtifactRequirement,
    BackendAdapter,
    BackendCapabilities,
    ExecutionResult,
)


# ── Module-level constants ─────────────────────────────────────────────────

BACKEND_ID = "csim"

COMPATIBLE_FLOW_KINDS: frozenset[str] = frozenset({
    "hls_csim",
})

REQUIRED_TOOLS: tuple[str, ...] = ()  # No vendor tools — native binary only


# ── Binary discovery ───────────────────────────────────────────────────────

def find_tb_binary(
    tb_name: str,
    consumer_root: "Path | None" = None,
) -> "Path | None":
    """Locate a compiled CSIM testbench binary.

    Search order:
      1. ``CSIM_TB_<TB_NAME_UPPER>`` environment variable
      2. ``<consumer_root>/build_targeted/<tb_name>``
      3. ``<consumer_root>/build/<tb_name>``
      4. PATH (shutil.which)

    Args:
        tb_name:       Testbench binary name (e.g. ``"tb_hit_decoder"``).
        consumer_root: Repo root; falls back to env var ``FW_CONSUMER_ROOT``
                       then the package's ancestor directory.
    """
    # 1. Explicit env override (e.g. CI sets CSIM_TB_TB_HIT_DECODER=/path/to/bin)
    env_key = f"CSIM_TB_{tb_name.upper().replace('-', '_')}"
    from_env = os.environ.get(env_key, "")
    if from_env:
        p = Path(from_env)
        if p.exists() and os.access(str(p), os.X_OK):
            return p

    # 2–3. Known build directories under consumer root
    root = consumer_root
    if root is None:
        root_from_env = os.environ.get("FW_CONSUMER_ROOT", "")
        root = Path(root_from_env) if root_from_env else Path(__file__).parents[4]

    for build_dir in ("build_targeted", "build"):
        candidate = root / build_dir / tb_name
        if candidate.exists() and os.access(str(candidate), os.X_OK):
            return candidate

    # 4. PATH
    on_path = shutil.which(tb_name)
    return Path(on_path) if on_path else None


# ── BackendAdapter implementation ──────────────────────────────────────────

class CsimBackend(BackendAdapter):
    """Generic HLS CSIM backend. Invokes the framework-standard XML dataset CLI."""

    @property
    def backend_id(self) -> str:
        return BACKEND_ID

    @property
    def compatible_flow_kinds(self) -> frozenset[str]:
        return COMPATIBLE_FLOW_KINDS

    @property
    def required_tools(self) -> tuple[str, ...]:
        return REQUIRED_TOOLS

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_waveform=False,
            supports_probe_log=False,
            requires_vendor_env=False,
            supports_checker_post_pass=True,
        )

    @property
    def required_artifacts(self) -> tuple[ArtifactRequirement, ...]:
        return (
            ArtifactRequirement("dataset_xml", path_attr="dataset_xml"),
        )

    def validate_backend_requirements(self, cfg: Any) -> list[str]:
        errors: list[str] = []
        consumer_root = getattr(cfg, "consumer_root", None)

        # ── 1. Binary discovery ──────────────────────────────────────────
        tb_bin = find_tb_binary(cfg.tb_module, consumer_root)
        if tb_bin is None:
            tb_env_key = f"CSIM_TB_{cfg.tb_module.upper().replace('-', '_')}"
            errors.append(
                f"CSIM testbench binary not found: {cfg.tb_module!r}\n"
                f"  Searched:\n"
                f"    1. ${tb_env_key} environment variable\n"
                f"    2. <consumer_root>/build_targeted/{cfg.tb_module}\n"
                f"    3. <consumer_root>/build/{cfg.tb_module}\n"
                f"    4. PATH (shutil.which)\n"
                f"  → Build the HLS testbench first (cmake --build build_targeted/)\n"
                f"  → Or set:  export {tb_env_key}=/path/to/binary"
            )

        # ── 2. Dataset XML exists ────────────────────────────────────────
        xml_input = getattr(cfg, "dataset_xml", None)
        if xml_input is not None:
            xml_path = Path(xml_input)
            if not xml_path.exists():
                errors.append(
                    f"Dataset XML not found: {xml_path}\n"
                    f"  Referenced in: design.verification.yml  datasets.xml\n"
                    f"  → Create or copy the XML dataset to: {xml_path}"
                )

        # ── 3. Command can be built ──────────────────────────────────────
        if tb_bin is not None and xml_input is not None:
            try:
                # Instantiate a temporary context-like object to test
                # _build_command — it must return a non-empty list.
                from forge.verify.runtime_context import RuntimeContext as _Ctx  # noqa: PLC0415
                _fake_ctx = _Ctx(work_dir=Path("/tmp"))
                cmd = self._build_command(cfg, _fake_ctx, tb_bin, Path(xml_input))
                if not cmd or not isinstance(cmd, list):
                    errors.append(
                        f"_build_command() returned an empty or invalid command list.\n"
                        f"  Override _build_command() in your CsimBackend subclass to "
                        f"return a non-empty list[str]."
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"_build_command() raised an exception: {exc}\n"
                    f"  This indicates a bug in the custom CsimBackend._build_command() "
                    f"override."
                )

        # ── 4. env_overrides are coherent ────────────────────────────────
        try:
            overrides = self.env_overrides(cfg)
            if not isinstance(overrides, dict):
                errors.append(
                    f"env_overrides() must return dict[str, str], "
                    f"got {type(overrides).__name__!r}"
                )
            else:
                for k, v in overrides.items():
                    if not isinstance(k, str) or not isinstance(v, str):
                        errors.append(
                            f"env_overrides() contains non-string entry: "
                            f"{k!r} → {v!r}  (both key and value must be str)"
                        )
                        break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"env_overrides() raised an exception: {exc}")

        return errors

    def prepare_backend_inputs(self, cfg: Any, ctx: Any) -> None:
        ctx.work_dir.mkdir(parents=True, exist_ok=True)

    def run_backend(self, cfg: Any, ctx: Any) -> ExecutionResult:
        """Run: ``<tb_binary> <dataset_xml>``."""
        consumer_root = getattr(cfg, "consumer_root", None)
        tb_bin = find_tb_binary(cfg.tb_module, consumer_root)
        if tb_bin is None:
            return ExecutionResult(
                success=False, exit_code=127,
                backend_metadata={"error": f"binary not found: {cfg.tb_module}"},
            )

        xml_input = getattr(ctx, "xml_input", None) or getattr(cfg, "dataset_xml", None)
        log_dir  = ctx.work_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        sim_log  = log_dir / "simulate.log"

        cmd      = self._build_command(cfg, ctx, tb_bin, xml_input)
        print(f"[csim] Running: {cfg.tb_module}")

        # Apply env_overrides: merge into the subprocess environment.
        overrides = self.env_overrides(cfg)
        env = None
        if overrides:
            env = dict(os.environ)
            env.update(overrides)

        exit_code = _run_logged(cmd, sim_log, env=env)

        result = ExecutionResult(
            success=(exit_code == 0),
            exit_code=exit_code,
            log_path=sim_log,
            backend_metadata={"tb_binary": str(tb_bin)},
        )
        # Allow plugin subclasses to post-process the result.
        return self.postprocess_result(cfg, ctx, result)

    def _build_command(
        self,
        cfg: Any,
        ctx: Any,
        tb_bin: Path,
        xml_input: Any,
    ) -> list[str]:
        """Return the command to invoke the testbench.

        **This is the only supported CLI customisation hook for csim.**

        Override in a plugin subclass to use a different CLI convention.
        Default: ``<tb_binary> <dataset_xml>``

        The returned list must be non-empty.  An empty list is treated as
        an error by :meth:`validate_backend_requirements`.
        """
        return [str(tb_bin), str(xml_input)]

    def env_overrides(self, cfg: Any) -> dict[str, str]:
        """Return environment variable overrides for the testbench process.

        The returned dict is merged into ``os.environ`` before invoking
        the subprocess.  Keys and values must be ``str``.

        Default returns an empty dict (no overrides applied).
        """
        return {}

    def postprocess_result(
        self,
        cfg: Any,
        ctx: Any,
        result: ExecutionResult,
    ) -> ExecutionResult:
        """Post-process the ``ExecutionResult`` after the binary finishes.

        Override to inspect logs, amend ``backend_metadata``, or adjust
        ``success`` based on plugin-specific success criteria beyond exit code.

        Default returns *result* unchanged.
        """
        return result

    def run_checker(self, cfg: Any, ctx: Any, result: ExecutionResult) -> bool:
        return result.success

    def describe_backend_outputs(self, cfg: Any, ctx: Any) -> dict[str, Path | None]:
        return {"simulate_log": ctx.work_dir / "simulate.log"}


# ── Subprocess helper ──────────────────────────────────────────────────────

def _run_logged(
    cmd: list[str],
    log_file: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Run *cmd*, capture output to *log_file*, return exit code.

    Delegates to :mod:`forge.verify.subprocess_wrapper` for structured capture
    (command, cwd, exit code, log path all recorded in SubprocessResult).
    Falls back to raw subprocess if env overrides are needed or wrapper import fails.

    Args:
        cmd:      Command + arguments list.
        log_file: Path to write combined stdout/stderr output.
        cwd:      Working directory override.
        env:      Complete environment dict override (``None`` = inherit os.environ).
    """
    import os as _os  # noqa: PLC0415
    try:
        from forge.verify.subprocess_wrapper import run_subprocess  # noqa: PLC0415
        # Compute env_overrides as a delta from os.environ
        env_overrides: dict[str, str] | None = None
        if env is not None:
            env_overrides = {
                k: v for k, v in env.items()
                if _os.environ.get(k) != v
            }
        result = run_subprocess(
            cmd,
            cwd=cwd or Path.cwd(),
            log_file=log_file,
            tool_name=cmd[0] if cmd else "csim",
            env_overrides=env_overrides or {},
            check=False,
        )
        return result.exit_code
    except ImportError:
        pass  # fallback
    # Fallback: raw subprocess with tee
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w") as lf:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(cwd) if cwd else None, text=True, env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            lf.write(line)
        proc.wait()
    return proc.returncode


# ── Module ADAPTER singleton ───────────────────────────────────────────────

ADAPTER = CsimBackend()
