#!/usr/bin/env python3
"""Generic Verilator simulation backend — framework-owned.

A plugin author does **not** need to write ``backend_verilator.py``.
This module is the default Verilator backend for RTL flow kinds that opt in
to ``backend: verilator`` (see ``forge.verification.supported_matrix``).

Responsibility boundary
-----------------------
This module owns the generic Verilator simulation lifecycle:

  * Tool-availability validation (``verilator`` on PATH)
  * Source-file-list generation from flow config (reusing
    :mod:`forge.verification.manifest_compile` for the manifest-driven case, the
    same helper :class:`~forge.verification.backend_xsim.XsimBackend` uses)
  * ``verilator --binary`` build + generated-binary execution pipeline
  * Log capture and pass/fail classification (via the checker stack lifted
    into :mod:`forge.verification.backend_checker` — identical
    ``log_scan``/``binary``/``none`` semantics to the xsim backend)

Waveform support
-----------------
``capabilities.supports_waveform`` is **False**.  This is not a shortcut —
it was tested empirically against the real passthrough_demo testbench:
``verilator --binary --trace-fst`` builds and runs cleanly, but produces no
``.fst`` file, because the framework's generated testbench never calls
``$dumpfile``/``$dumpvars`` (nothing about tracing is simulator-agnostic
today; xsim's ``wave.tcl`` mechanism is xsim-specific). Claiming
``supports_waveform=True`` here would be exactly the unproven claim rule 11
forbids. Revisit once a real, tested trace-enabling mechanism exists.

Two-stage pipeline (not three, unlike xsim)
--------------------------------------------
Verilator has no separate elaborate step — ``verilator --binary`` compiles
*and* elaborates in one invocation, producing a native executable directly.
So this backend only ever reports :class:`~forge.verification.execution_stage.ExecutionStage`
``COMPILE`` (the build) and ``SIMULATE`` (running the built binary) — never
``ELABORATE``.

ADAPTER singleton
-----------------
``ADAPTER = VerilatorBackend()`` — entry point for backend_registry.
"""
from __future__ import annotations

import re
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

BACKEND_ID          = "verilator"
SOURCES_FILE         = "verilator_sources.f"

COMPATIBLE_FLOW_KINDS: frozenset[str] = frozenset({
    "full_chip_rtl",
    "single_module_rtl",
})

REQUIRED_TOOLS: tuple[str, ...] = ("verilator",)

_PRJ_ENTRY_PATH_RE = re.compile(r'"([^"]+)"\s*$')


def snapshot_name(tb_module: str) -> str:
    """Return the Verilator build-output identifier for *tb_module*."""
    return f"{tb_module}_vsim"


def _paths_from_prj_entries(entries: list[str]) -> list[Path]:
    """Extract bare file paths from ``manifest_compile.py``'s ``sv/verilog
    xil_defaultlib "<path>"`` entry strings.

    Reuses :func:`forge.verification.manifest_compile.prepare_from_manifest` (HLS
    module expansion, dedup, extra-source handling, testbench-last
    ordering, ``.dat`` staging) rather than re-implementing manifest
    walking here; this just re-reads its xsim-flavoured entry strings back
    into plain paths for a Verilator command line.
    """
    paths: list[Path] = []
    for entry in entries:
        m = _PRJ_ENTRY_PATH_RE.search(entry)
        if m:
            paths.append(Path(m.group(1)))
    return paths


# ── BackendAdapter implementation ──────────────────────────────────────────

class VerilatorBackend(BackendAdapter):
    """Generic Verilator backend for RTL flow kinds."""

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
            supports_waveform=False,   # empirically unproven — see module docstring
            supports_probe_log=False,  # untested under Verilator in this slice
            requires_vendor_env=False,
            supports_checker_post_pass=True,
        )

    @property
    def required_artifacts(self) -> tuple[ArtifactRequirement, ...]:
        return (
            ArtifactRequirement("rtl_top", path_attr="dut_rtl"),
        )

    def validate_backend_requirements(self, cfg: Any) -> list[str]:
        errors: list[str] = []

        # ── 1. verilator on PATH ───────────────────────────────────────────
        for tool in REQUIRED_TOOLS:
            if not shutil.which(tool):
                errors.append(
                    f"Verilator tool not found on PATH: {tool!r}\n"
                    f"  → Install Verilator, e.g.:\n"
                    f"      apt-get install verilator   (Ubuntu/Debian)\n"
                    f"      dnf install verilator       (Fedora/RHEL)\n"
                    f"      brew install verilator      (macOS)"
                )

        # ── 2. Canonical flow directory layout ────────────────────────────
        flow_dir = Path(cfg.flow_file).parent
        verify_root = flow_dir.parent
        from forge.verification.layout import validate_layout  # noqa: PLC0415
        layout_errors = validate_layout(verify_root)
        for le in layout_errors:
            errors.append(f"[layout] {le}")

        # ── 3. Generated TB exists ────────────────────────────────────────
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

        # ── 5. stimulus_current.svh exists ────────────────────────────────
        from forge.verification.stimulus_contract import STIMULUS_FILE  # noqa: PLC0415
        stim_svh = flow_dir / STIMULUS_FILE
        if not stim_svh.exists():
            errors.append(
                f"stimulus_current.svh not found: {stim_svh}\n"
                f"  → Generate it first: python3 tools/gen_stimulus.py"
                f" --flow {getattr(cfg, 'flow_name', cfg.tb_module)}"
            )
        else:
            # ── 6. Stimulus contract validation ───────────────────────────
            from forge.verification.stimulus_contract import validate_stimulus  # noqa: PLC0415
            sc = validate_stimulus(stim_svh)
            if not sc.ok:
                for e in sc.errors:
                    errors.append(e)

        return errors

    # ── Override hook: extra RTL sources (mirrors XsimBackend) ────────────

    def extra_rtl_sources(self, cfg: Any) -> "list[tuple[Path, str]]":
        """Return ``[(path, lang), ...]`` entries appended to the source list.

        Default returns an empty list.
        """
        return []

    # ── Override hook: custom checker log patterns ────────────────────────

    def custom_checker_patterns(self, cfg: Any) -> list[str]:
        """Return additional failure marker strings for log scanning.

        Default returns an empty list.
        """
        return []

    def prepare_backend_inputs(self, cfg: Any, ctx: Any) -> None:
        """Write a Verilator ``-f`` command file listing include dirs and sources.

        If :attr:`~forge.verification.flow_loader.FlowConfig.dut_manifest` is set,
        delegates the manifest walk itself to
        :func:`forge.verification.manifest_compile.prepare_from_manifest` (same
        helper xsim uses) and only reformats its output for Verilator.
        Otherwise writes the minimal two-file (TB + DUT) source list.
        """
        work_dir = Path(ctx.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        flow_dir = Path(cfg.flow_file).parent.resolve()

        include_dirs: list[str] = [str(flow_dir)]
        sources: list[Path]

        manifest = getattr(cfg, "dut_manifest", None)
        if manifest is not None:
            from forge.verification.manifest_compile import prepare_from_manifest  # noqa: PLC0415
            stim_dir = flow_dir / "stimulus"
            stim_dir.mkdir(parents=True, exist_ok=True)
            tb_file  = flow_dir / f"{cfg.tb_module}.sv"
            rtl_dir  = Path(cfg.dut_rtl).parent.resolve()
            extra    = self.extra_rtl_sources(cfg)
            result = prepare_from_manifest(
                manifest_path = manifest,
                tb_file       = tb_file,
                extra_sources = extra,
                stim_dir      = stim_dir,
                bindings_dir  = rtl_dir,
                work_dir      = work_dir,
                stage_dat     = True,
            )
            sources = _paths_from_prj_entries(result["entries"])
            include_dirs += result["include_dirs"]
        else:
            tb_sv   = flow_dir / f"{cfg.tb_module}.sv"
            sources = [tb_sv]
            dut_rtl = cfg.dut_rtl
            if dut_rtl is not None:
                sources.append(Path(dut_rtl))
            for path, lang in self.extra_rtl_sources(cfg):
                sources.append(Path(path))

        lines = [f'-I"{d}"' for d in include_dirs]
        lines += [f'"{p}"' for p in sources]
        (work_dir / SOURCES_FILE).write_text("\n".join(lines) + "\n")

    def run_backend(self, cfg: Any, ctx: Any) -> ExecutionResult:
        """Execute the two-stage Verilator pipeline: build, then run.

        When ``cfg.stimulus_mode == "readmemh"``, the build
        step is skipped once a real, previously-successful build sentinel
        exists in ``work_dir`` — mirrors ``XsimBackend``'s identical
        compile-once-run-many-events discipline.
        """
        work_dir  = Path(ctx.work_dir).resolve()
        obj_dir   = work_dir / "obj_dir"
        snapshot  = snapshot_name(cfg.tb_module)
        sources_f = work_dir / SOURCES_FILE

        verilate_log = work_dir / "verilate.log"
        simulate_log = work_dir / "simulate.log"

        readmemh_mode = getattr(cfg, "stimulus_mode", "svh_include") == "readmemh"
        build_sentinel = work_dir / ".build_complete"
        binary = obj_dir / snapshot

        if readmemh_mode and build_sentinel.exists() and binary.exists():
            print("[verilator 1/2] Reusing existing build (stimulus_mode=readmemh)")
        else:
            # ── verilator --binary ────────────────────────────────────────
            print(f"[verilator 1/2] Compiling+Elaborating ({cfg.tb_module})")
            verilate_cmd = [
                "verilator", "--binary", "--timing", "--Wno-fatal",
                "-f", str(sources_f),
                "--top-module", cfg.tb_module,
                "-o", snapshot,
                "--Mdir", str(obj_dir),
            ]
            if getattr(ctx, "probe_log", False):
                verilate_cmd += ["-DPROBE_LOG=1"]

            run = _run_logged(verilate_cmd, verilate_log, cwd=work_dir)
            if run.exit_code != 0:
                return ExecutionResult(
                    success=False, exit_code=run.exit_code, log_path=verilate_log,
                    stage=ExecutionStage.COMPILE, duration_s=run.elapsed_s,
                    backend_id=BACKEND_ID,
                    backend_metadata={"step": "verilate"},
                )
            if readmemh_mode:
                build_sentinel.write_text("1")

        # ── run the built binary ──────────────────────────────────────────
        print("[verilator 2/2] Simulating")
        run_cmd = [str(binary)]
        event_index = getattr(ctx, "event_index", None)
        if readmemh_mode and event_index is not None:
            run_cmd += [f"+EVENT_INDEX={event_index}"]
        run = _run_logged(run_cmd, simulate_log, cwd=work_dir)

        return ExecutionResult(
            success=(run.exit_code == 0),
            exit_code=run.exit_code,
            log_path=simulate_log,
            stage=ExecutionStage.SIMULATE,
            duration_s=run.elapsed_s,
            backend_id=BACKEND_ID,
            waveform_path=None,  # capabilities.supports_waveform is False — never fabricate one
            backend_metadata={
                "step":     "simulate",
                "snapshot": snapshot,
                "work_dir": str(work_dir),
            },
        )

    def run_checker(self, cfg: Any, ctx: Any, result: ExecutionResult) -> bool:
        """Thin delegate to the simulator-agnostic checker stack."""
        return backend_checker.run_checker(self, cfg, ctx, result)

    def checker_binary_path(self, cfg: Any) -> "Path | None":
        return backend_checker.checker_binary_path(cfg)

    def checker_command_args(
        self, cfg: Any, ctx: Any, result: ExecutionResult,
    ) -> list[str]:
        return backend_checker.checker_command_args(cfg, ctx, result)

    def describe_backend_outputs(self, cfg: Any, ctx: Any) -> dict[str, Path | None]:
        return {
            "verilate_log": ctx.work_dir / "verilate.log",
            "simulate_log": ctx.work_dir / "simulate.log",
        }


# ── Helpers ─────────────────────────────────────────────────────────────────

class LoggedRun(NamedTuple):
    """Result of one :func:`_run_logged` invocation."""
    exit_code: int
    elapsed_s: float


def _run_logged(cmd: list[str], log_file: Path, cwd: Path | None = None) -> LoggedRun:
    """Run *cmd*, capture output to *log_file*, return exit code + duration.

    Delegates to :mod:`forge.verification.subprocess_wrapper`, matching the xsim
    and csim backends' convention exactly.
    """
    try:
        from forge.verification.subprocess_wrapper import run_subprocess  # noqa: PLC0415
        result = run_subprocess(
            cmd,
            cwd=cwd or Path.cwd(),
            log_file=log_file,
            tool_name=cmd[0] if cmd else "verilator",
            check=False,
        )
        return LoggedRun(result.exit_code, result.elapsed_s)
    except ImportError:
        pass  # fallback
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

ADAPTER = VerilatorBackend()
