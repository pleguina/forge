#!/usr/bin/env python3
"""Generic Xilinx XSIM backend — framework-owned.

A plugin author does **not** need to write ``backend_xsim.py``.
This module is the default xsim backend for all plugins that use the
``xsim`` simulator without plugin-specific overrides.

Responsibility boundary
-----------------------
This module owns the generic xsim simulation lifecycle:

  * Tool-availability validation (xvlog, xelab, xsim)
  * Compile-project file generation from flow config
  * xvlog → xelab → xsim execution pipeline
  * Log capture and pass/fail classification
  * Waveform artifact reporting

Plugin-specific concerns NOT owned here
----------------------------------------
  * Stimulus generation   → plugin stimulus generator (gen_xsim.py etc.)
  * Checker executable    → plugin-owned or declared in verify.flow.yml
  * Custom compile flags  → can be supplied via flow YAML fields (future)
  * Non-generic port setup → plugin can subclass XsimBackend if needed

Override policy
---------------
A plugin may still ship ``backend_xsim.py`` with a custom ``ADAPTER`` to
override the framework default.  When the plugin registers a custom module
via ``register_backend("xsim", "my_backend_xsim")``, that takes precedence
over the framework default.  This module is only used when no plugin-local
override is registered.

Auto-registration
-----------------
This module is auto-registered by ``forge verify`` at package import time.
No plugin ``bootstrap.py`` needs to call ``register_backend("xsim", ...)``.
Plugins that wish to override may re-register after import.

ADAPTER singleton
-----------------
``ADAPTER = XsimBackend()`` — entry point for backend_registry.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

from forge.verification import backend_checker
from forge.verification.backend_base import (
    ArtifactRequirement,
    BackendAdapter,
    BackendCapabilities,
    ExecutionResult,
)
from forge.verification.execution_stage import ExecutionStage


# ── Module-level constants ─────────────────────────────────────────────────

BACKEND_ID            = "xsim"
COMPILE_PROJECT_FILE  = "compile.prj"

COMPATIBLE_FLOW_KINDS: frozenset[str] = frozenset({
    "full_chip_rtl",
    "reduced_chain_rtl",
    "single_module_rtl",
})

REQUIRED_TOOLS: tuple[str, ...] = ("xvlog", "xelab", "xsim")


def snapshot_name(tb_module: str) -> str:
    """Return the xsim snapshot identifier for *tb_module*."""
    return f"{tb_module}_snapshot"


# ── BackendAdapter implementation ──────────────────────────────────────────

class XsimBackend(BackendAdapter):
    """Generic Xilinx XSIM backend.  Handles all standard RTL flow kinds."""

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
            supports_waveform=True,
            supports_probe_log=True,
            requires_vendor_env=True,
            supports_checker_post_pass=True,
        )

    @property
    def required_artifacts(self) -> tuple[ArtifactRequirement, ...]:
        return (
            ArtifactRequirement("rtl_top", path_attr="dut_rtl"),
        )

    def validate_backend_requirements(self, cfg: Any) -> list[str]:
        errors: list[str] = []

        # ── 1. Vendor tools on PATH ────────────────────────────────────────
        for tool in REQUIRED_TOOLS:
            if not shutil.which(tool):
                errors.append(
                    f"XSIM tool not found on PATH: {tool!r}\n"
                    f"  → Source the Xilinx Vivado/Vitis environment:\n"
                    f"      source <vivado_install>/settings64.sh"
                )

        # ── 2. Canonical flow directory layout ────────────────────────────
        flow_dir = Path(cfg.flow_file).parent
        verify_root = flow_dir.parent
        from forge.verification.layout import validate_layout  # noqa: PLC0415
        layout_errors = validate_layout(verify_root)
        for le in layout_errors:
            errors.append(f"[layout] {le}")

        # ── 3. Generated TB exists ────────────────────────────────────────
        # Use the stimulus_contract constant for the canonical TB naming
        tb_sv = flow_dir / f"{cfg.tb_module}.sv"
        if not tb_sv.exists():
            errors.append(
                f"SV testbench not found: {tb_sv}\n"
                f"  → Regenerate with: forge verify generate design.verification.yml"
                f" --flow {getattr(cfg, 'flow_name', cfg.tb_module)}"
            )

        # ── 4. DUT RTL source exists ───────────────────────────────────────
        dut_rtl = getattr(cfg, "dut_rtl", None)
        if dut_rtl is not None:
            dut_rtl_path = Path(dut_rtl)
            if not dut_rtl_path.exists():
                errors.append(
                    f"DUT RTL file not found: {dut_rtl_path}\n"
                    f"  → Run HLS synthesis first:\n"
                    f"      forge verify generate uses dut_rtl_source from design.verification.yml\n"
                    f"  RTL expected at: {dut_rtl_path}"
                )

        # ── 5. wave.tcl exists (if wave_mode requires it) ─────────────────
        wave_mode = getattr(cfg, "wave_mode", "tcl")
        if wave_mode == "tcl":
            wave_tcl = flow_dir / "wave.tcl"
            if not wave_tcl.exists():
                errors.append(
                    f"wave.tcl not found: {wave_tcl}\n"
                    f"  → Regenerate with: forge verify generate design.verification.yml"
                    f" --flow {getattr(cfg, 'flow_name', cfg.tb_module)}\n"
                    f"  (or set wave_mode: none in design.verification.yml to disable waveforms)"
                )

        # ── 6. stimulus_current.svh exists ────────────────────────────────
        from forge.verification.stimulus_contract import STIMULUS_FILE  # noqa: PLC0415
        stim_svh = flow_dir / STIMULUS_FILE
        if not stim_svh.exists():
            errors.append(
                f"stimulus_current.svh not found: {stim_svh}\n"
                f"  → Generate it first: python3 tools/gen_stimulus.py"
                f" --flow {getattr(cfg, 'flow_name', cfg.tb_module)}"
            )
        else:
            # ── 7. Stimulus contract validation ───────────────────────────
            from forge.verification.stimulus_contract import validate_stimulus  # noqa: PLC0415
            sc = validate_stimulus(stim_svh)
            if not sc.ok:
                for e in sc.errors:
                    errors.append(e)

        return errors

    # ── Override hook: extra include directories ──────────────────────────

    def extra_include_dirs(self, cfg: Any) -> list[str]:
        """Return additional ``--include`` directories for xvlog.

        Override in a plugin subclass to add plugin-specific include paths
        (e.g. shared SV packages) without replacing the entire compile step.

        Default returns an empty list.
        """
        work_dir = Path(cfg.flow_file).parent / "xsim_work"
        idf = work_dir / "include_dirs.txt"
        if idf.exists():
            return [ln.strip() for ln in idf.read_text().splitlines() if ln.strip()]
        return []

    # ── Override hook: extra compile flags ────────────────────────────────

    def extra_compile_flags(self, cfg: Any) -> list[str]:
        """Return additional flags appended to the xvlog command line.

        Default returns an empty list.
        """
        return []

    # ── Override hook: xvlog --sv flag ────────────────────────────────────

    def xvlog_use_sv_flag(self, cfg: Any) -> bool:
        """Return False to omit ``--sv`` from xvlog.

        When ``compile.prj`` specifies the HDL language per-file (e.g. when
        built from a ``build_manifest.json`` that mixes Verilog and SV files),
        passing ``--sv`` globally causes xvlog to reject Verilog-mode files.
        Override to return ``False`` in that case.

        Default returns ``True`` (global ``--sv`` flag).
        """
        xcfg = getattr(cfg, "xsim", None)
        if xcfg is not None:
            return bool(getattr(xcfg, "use_sv_flag", True))
        return True

    # ── Override hook: extra elaboration flags ────────────────────────────

    def extra_elab_flags(self, cfg: Any) -> list[str]:
        """Return additional flags appended to the xelab command line.

        Default returns an empty list.
        """
        xcfg = getattr(cfg, "xsim", None)
        if xcfg is not None:
            libs = list(getattr(xcfg, "elab_libs", ()) or [])
            result: list[str] = []
            for lib in libs:
                result += ["-L", lib]
            return result
        return []

    # ── Override hook: extra elaboration library flags ────────────────────

    def extra_elab_lib_flags(self, cfg: Any) -> list[str]:
        """Return ``["-L", "libname", ...]`` pairs inserted into xelab BEFORE
        the top-module names.

        Use this for Xilinx simulation libraries that must appear before
        the top module argument (e.g. ``unisims_ver``, ``unimacro_ver``,
        ``secureip``).

        Default returns an empty list.
        """
        xcfg = getattr(cfg, "xsim", None)
        if xcfg is not None:
            return list(getattr(xcfg, "extra_top_modules", ()) or [])
        return []

    # ── Override hook: extra xelab top modules ────────────────────────────

    def extra_top_modules(self, cfg: Any) -> list[str]:
        """Return additional top-level module names for xelab.

        These are inserted after the primary top module and before ``-s``.
        Use when the simulator requires helper modules to be co-elaborated
        (e.g. ``xil_defaultlib.glbl`` for Xilinx IPs).

        Default returns an empty list.
        """
        return []

    # ── Override hook: xelab top module name ─────────────────────────────

    def qualify_top_name(self, cfg: Any) -> str:
        """Return the fully-qualified top-module name passed to xelab.

        By default returns ``cfg.tb_module`` (unqualified).  Override to
        prepend a library qualifier, e.g. ``"xil_defaultlib.tb_foo"``.
        """
        xcfg = getattr(cfg, "xsim", None)
        top_lib = getattr(xcfg, "top_lib", None) if xcfg is not None else None
        if top_lib:
            return f"{top_lib}.{cfg.tb_module}"
        return cfg.tb_module

    # ── Override hook: build manifest path ───────────────────────────────

    def build_manifest_path(self, cfg: Any) -> "Path | None":
        """Return the path to a ``build_manifest.json`` for this flow, or
        ``None`` (default) to use the simple two-file compile.prj.

        When non-None, ``prepare_backend_inputs`` will call
        :func:`forge.verification.manifest_compile.prepare_from_manifest` to build a
        full compile.prj from the manifest instead of the generic two-file
        version.
        """
        return getattr(cfg, "dut_manifest", None)

    # ── Override hook: extra RTL sources (glbl, fp stub, …) ──────────────

    def extra_rtl_sources(self, cfg: Any) -> "list[tuple[Path, str]]":
        """Return ``[(path, lang), ...]`` entries appended to compile.prj.

        ``lang`` must be ``"sv"`` or ``"verilog"``.
        Used for optional Xilinx helper files such as ``glbl.v`` and
        floating-point IP stubs that should not appear in the manifest.

        Default returns an empty list.
        """
        xcfg = getattr(cfg, "xsim", None)
        if xcfg is not None:
            return [(src.path, src.lang) for src in (getattr(xcfg, "extra_sources", ()) or [])]
        return []

    # ── Override hook: custom checker log patterns ────────────────────────

    def custom_checker_patterns(self, cfg: Any) -> list[str]:
        """Return additional failure marker strings for log scanning.

        These are added to the built-in set (``$fatal``, ``ASSERTION FAILED``,
        ``TEST FAILED``, ``FAIL:``).  Override in a plugin subclass to add
        testbench-specific failure strings.

        Default returns an empty list.
        """
        return []

    # ── Override hook: post-run artifacts ─────────────────────────────────

    def post_run_artifacts(
        self,
        cfg: Any,
        ctx: Any,
        result: "ExecutionResult",
    ) -> dict[str, "Path | None"]:
        """Return plugin-specific artifacts produced after simulation.

        Called by :meth:`run_checker` after pass/fail determination.
        The returned dict is merged into the artifact summary printed by the
        ``run`` command.

        Default returns an empty dict.
        """
        return {}

    def prepare_backend_inputs(self, cfg: Any, ctx: Any) -> None:
        """Write compile.prj listing the DUT RTL and SV testbench sources.

        If :meth:`build_manifest_path` returns a path, delegates to
        :mod:`forge.verification.manifest_compile` for manifest-based multi-source
        compilation.  Otherwise writes a minimal two-file compile.prj.
        """
        work_dir = Path(ctx.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)

        manifest = self.build_manifest_path(cfg)
        if manifest is not None:
            from forge.verification.manifest_compile import prepare_from_manifest  # noqa: PLC0415
            flow_dir = Path(cfg.flow_file).parent.resolve()
            stim_dir = flow_dir / "stimulus"
            stim_dir.mkdir(parents=True, exist_ok=True)
            tb_file  = flow_dir / f"tb_{cfg.top_module}.sv"
            rtl_dir  = Path(cfg.dut_rtl).parent.resolve()
            extra    = self.extra_rtl_sources(cfg)
            prepare_from_manifest(
                manifest_path = manifest,
                tb_file       = tb_file,
                extra_sources = extra,
                stim_dir      = stim_dir,
                bindings_dir  = rtl_dir,
                work_dir      = work_dir,
                stage_dat     = True,
            )
            return

        flow_dir = Path(cfg.flow_file).parent
        tb_sv    = flow_dir / f"{cfg.tb_module}.sv"
        dut_rtl  = cfg.dut_rtl

        prj_path = work_dir / COMPILE_PROJECT_FILE
        lines: list[str] = [f'sv      xil_defaultlib "{tb_sv}"']
        if dut_rtl is not None:
            lines.append(f'verilog xil_defaultlib "{dut_rtl}"')
        for path, lang in self.extra_rtl_sources(cfg):
            lines.append(f'{lang} xil_defaultlib "{path}"')
        prj_path.write_text("\n".join(lines) + "\n")

    def run_backend(self, cfg: Any, ctx: Any) -> ExecutionResult:
        """Execute xvlog → xelab → xsim, honouring override hooks.

        When ``cfg.stimulus_mode == "readmemh"`` (slice 7.5), xvlog/xelab
        are skipped once a real, previously-successful build sentinel
        exists in ``work_dir`` — the fixed-shape stimulus mechanism's
        whole point is compiling once and reselecting which event runs via
        the ``+EVENT_INDEX`` plusarg, not recompiling per event. Flows on
        the default ``svh_include`` mechanism always recompile, unchanged.
        """
        work_dir = Path(ctx.work_dir).resolve()
        flow_dir = Path(cfg.flow_file).parent.resolve()
        prj_file = work_dir / COMPILE_PROJECT_FILE
        snapshot = snapshot_name(cfg.tb_module)
        wave_tcl = _locate_wave_tcl(cfg, flow_dir)

        xvlog_log    = work_dir / "xvlog.log"
        xelab_log    = work_dir / "xelab.log"
        simulate_log = work_dir / "simulate.log"

        readmemh_mode = getattr(cfg, "stimulus_mode", "svh_include") == "readmemh"
        build_sentinel = work_dir / ".compile_elaborate_complete"

        if readmemh_mode and build_sentinel.exists():
            print(f"[xsim 1-2/3] Reusing existing compile+elaborate (stimulus_mode=readmemh)")
        else:
            # ── xvlog ──────────────────────────────────────────────────────
            print(f"[xsim 1/3] Compiling  ({cfg.tb_module})")
            xvlog_cmd = ["xvlog", "--incr", "--relax"]
            if self.xvlog_use_sv_flag(cfg):
                xvlog_cmd.append("--sv")
            xvlog_cmd += ["--include", str(flow_dir)]
            for inc_dir in self.extra_include_dirs(cfg):
                xvlog_cmd += ["--include", str(inc_dir)]
            xvlog_cmd += self.extra_compile_flags(cfg)
            if getattr(ctx, "probe_log", False):
                xvlog_cmd += ["-d", "PROBE_LOG=1"]
            xvlog_cmd += ["-prj", str(prj_file)]

            run = _run_logged(xvlog_cmd, xvlog_log, cwd=work_dir)
            if run.exit_code != 0:
                return ExecutionResult(
                    success=False, exit_code=run.exit_code, log_path=xvlog_log,
                    stage=ExecutionStage.COMPILE, duration_s=run.elapsed_s,
                    backend_id=BACKEND_ID,
                    backend_metadata={"step": "xvlog"},
                )

            # ── xelab ──────────────────────────────────────────────────────
            print(f"[xsim 2/3] Elaborating (snapshot={snapshot})")
            xelab_cmd = [
                "xelab", "--incr", "--debug", "typical", "--relax",
                "-L", "xil_defaultlib",
            ]
            xelab_cmd += self.extra_elab_lib_flags(cfg)
            xelab_cmd += self.extra_elab_flags(cfg)
            xelab_cmd.append(self.qualify_top_name(cfg))
            xelab_cmd += self.extra_top_modules(cfg)
            xelab_cmd += ["-s", snapshot]
            run = _run_logged(xelab_cmd, xelab_log, cwd=work_dir)
            if run.exit_code != 0:
                return ExecutionResult(
                    success=False, exit_code=run.exit_code, log_path=xelab_log,
                    stage=ExecutionStage.ELABORATE, duration_s=run.elapsed_s,
                    backend_id=BACKEND_ID,
                    backend_metadata={"step": "xelab"},
                )

            if readmemh_mode:
                build_sentinel.write_text("1")

        # ── xsim ───────────────────────────────────────────────────────────
        print("[xsim 3/3] Simulating")
        xsim_cmd = ["xsim", snapshot, "-log", str(simulate_log)]
        if wave_tcl is not None:
            staged_wave_tcl = work_dir / wave_tcl.name
            if wave_tcl.resolve() != staged_wave_tcl.resolve():
                shutil.copy2(wave_tcl, staged_wave_tcl)
            xsim_cmd += ["-tclbatch", str(staged_wave_tcl)]
        event_index = getattr(ctx, "event_index", None)
        if readmemh_mode and event_index is not None:
            xsim_cmd += ["-testplusarg", f"EVENT_INDEX={event_index}"]
        run = _run_logged(xsim_cmd, simulate_log, cwd=work_dir)

        waveform_path: Path | None = None
        if self.capabilities.supports_waveform:
            candidate = work_dir / f"{snapshot}.wdb"
            if candidate.exists():
                waveform_path = candidate

        return ExecutionResult(
            success=(run.exit_code == 0),
            exit_code=run.exit_code,
            log_path=simulate_log,
            stage=ExecutionStage.SIMULATE,
            duration_s=run.elapsed_s,
            backend_id=BACKEND_ID,
            waveform_path=waveform_path,
            backend_metadata={
                "step":     "xsim",
                "snapshot": snapshot,
                "work_dir": str(work_dir),
            },
        )

    def run_checker(self, cfg: Any, ctx: Any, result: ExecutionResult) -> bool:
        """Determine pass/fail according to ``checker_mode`` in the flow config.

        Thin delegate to the simulator-agnostic checker stack in
        :mod:`forge.verification.backend_checker` — see that module for the
        ``"log_scan"``/``"binary"``/``"none"`` behavior.
        """
        return backend_checker.run_checker(self, cfg, ctx, result)

    # ── Override hook: checker binary location ────────────────────────────

    def checker_binary_path(self, cfg: Any) -> "Path | None":
        """Return the path to the external checker binary, or ``None``.

        Called by the lifted checker stack when ``checker_mode == "binary"``.
        Override in a plugin subclass to locate the checker binary (e.g. from
        a build directory or environment variable).

        Default delegates to :func:`forge.verification.backend_checker.checker_binary_path`.
        """
        return backend_checker.checker_binary_path(cfg)

    # ── Override hook: checker command arguments ──────────────────────────

    def checker_command_args(
        self,
        cfg: Any,
        ctx: Any,
        result: ExecutionResult,
    ) -> list[str]:
        """Return CLI arguments for the external checker binary.

        Called by the lifted checker stack after :meth:`checker_binary_path`.
        Override to provide the checker-specific argument list.

        Default delegates to :func:`forge.verification.backend_checker.checker_command_args`.
        """
        return backend_checker.checker_command_args(cfg, ctx, result)

    def describe_backend_outputs(self, cfg: Any, ctx: Any) -> dict[str, Path | None]:
        return {
            "xvlog_log":    ctx.work_dir / "xvlog.log",
            "xelab_log":    ctx.work_dir / "xelab.log",
            "simulate_log": ctx.work_dir / "simulate.log",
        }


# ── Helpers ─────────────────────────────────────────────────────────────────

def _locate_wave_tcl(cfg: Any, flow_dir: Path) -> Path | None:
    """Return the wave.tcl path for this flow, or None if not present."""
    explicit = getattr(cfg, "outputs_waveform_tcl", None)
    if explicit is not None:
        return Path(explicit)
    candidate = flow_dir / "wave.tcl"
    return candidate if candidate.exists() else None


class LoggedRun(NamedTuple):
    """Result of one :func:`_run_logged` invocation."""
    exit_code: int
    elapsed_s: float


def _run_logged(cmd: list[str], log_file: Path, cwd: Path | None = None) -> LoggedRun:
    """Run *cmd*, capture output to *log_file*, return exit code + duration.

    Delegates to :mod:`forge.verification.subprocess_wrapper` for structured capture
    (command, cwd, exit code, log path, elapsed time all recorded in
    SubprocessResult). Falls back to raw subprocess if wrapper import fails.
    """
    try:
        from forge.verification.subprocess_wrapper import run_subprocess  # noqa: PLC0415
        result = run_subprocess(
            cmd,
            cwd=cwd or Path.cwd(),
            log_file=log_file,
            tool_name=cmd[0] if cmd else "xsim",
            check=False,
        )
        return LoggedRun(result.exit_code, result.elapsed_s)
    except ImportError:
        pass  # fallback
    # Fallback: raw subprocess with tee
    log_file.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with open(log_file, "w") as lf:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(cwd) if cwd else None, text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            lf.write(line)
        proc.wait()
    return LoggedRun(proc.returncode, time.monotonic() - t0)


# ── Module ADAPTER singleton ───────────────────────────────────────────────

ADAPTER = XsimBackend()
