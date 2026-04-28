"""arc hls — Vitis HLS build commands."""

from __future__ import annotations

import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_hls_templates_dir() -> "Path | None":
    """Locate the HLS template directory.

    Resolution order:
    1. ``TOPGEN_HLS_TEMPLATES`` environment variable
    2. repo-layout walk-up fallback (for monorepo/source-tree use)
    """
    import os as _os

    env_override = _os.environ.get("TOPGEN_HLS_TEMPLATES")
    if env_override:
        candidate = Path(env_override).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    for start in (Path.cwd(), Path(__file__).resolve()):
        p = start
        for _ in range(15):
            candidate = p / "hls" / "templates"
            if candidate.is_dir():
                return candidate
            if p.parent == p:
                break
            p = p.parent
    return None


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_hls_gen_tcl(args):
    """Generate Vitis HLS TCL scripts for one or all catalog modules."""
    from arc.hls.catalog import (
        load_hls_catalog,
        generate_tcl,
    )

    try:
        catalog = load_hls_catalog(args.hls_config)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    template_dir = getattr(args, "template_dir", None)
    if template_dir is None:
        template_dir = _find_hls_templates_dir()
        if template_dir is None:
            print(
                "ERROR: cannot locate hls/templates/. "
                "Pass --template-dir explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)

    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else Path("build_hls")
    ip_packages_dir = Path(args.ip_packages_dir) if getattr(args, "ip_packages_dir", None) else None

    config_override: dict = {}
    if getattr(args, "flow", None):
        config_override["flow"] = args.flow

    module_arg = getattr(args, "module", None) or "all"
    modules = catalog["ordered_modules"] if module_arg == "all" else [module_arg]

    ok = True
    for module_name in modules:
        print(f"\n=== Generating TCL for {module_name} ===")
        if not generate_tcl(catalog, module_name, template_dir, output_dir, ip_packages_dir, config_override):
            ok = False

    sys.exit(0 if ok else 1)


def cmd_hls_run(args):
    """Run parallel Vitis HLS jobs — pure Python, no shell script required."""
    import concurrent.futures
    import subprocess

    from arc.hls.catalog import load_hls_catalog

    # ── Resolve inputs ────────────────────────────────────────────────────
    registry_path = (
        Path(args.registry).expanduser().resolve()
        if getattr(args, "registry", None)
        else None
    )
    build_root = (
        Path(args.hls_build_root).expanduser().resolve()
        if getattr(args, "hls_build_root", None)
        else Path("build_hls").resolve()
    )
    ip_pack_dir = (
        Path(args.ip_packages_dir).expanduser().resolve()
        if getattr(args, "ip_packages_dir", None)
        else build_root / "ip_packages"
    )
    vitis_hls = (
        str(Path(args.vitis_hls).expanduser().resolve())
        if getattr(args, "vitis_hls", None)
        else "vitis_hls"
    )
    stage = getattr(args, "stage", None)
    modules_arg = getattr(args, "modules", None) or "all"
    jobs = getattr(args, "jobs", None) or 1
    stages_arg = getattr(args, "stages", None)

    # ── Load catalog if provided ──────────────────────────────────────────
    catalog = None
    if registry_path and registry_path.exists():
        try:
            catalog = load_hls_catalog(str(registry_path))
        except Exception as exc:
            print(f"ERROR loading registry {registry_path}: {exc}", file=sys.stderr)
            sys.exit(1)

    # ── Resolve module list ───────────────────────────────────────────────
    if modules_arg == "all":
        if catalog is None:
            print("ERROR: --registry is required when --modules all is used.", file=sys.stderr)
            sys.exit(1)
        module_list = catalog["ordered_modules"]
    else:
        module_list = [m.strip() for m in modules_arg.replace(",", " ").split() if m.strip()]

    if not module_list:
        print("ERROR: no modules resolved.", file=sys.stderr)
        sys.exit(1)

    # ── Resolve stages ────────────────────────────────────────────────────
    if stages_arg:
        stage_list = [s.strip() for s in stages_arg.replace(",", " ").split() if s.strip()]
    elif stage:
        stage_list = [stage]
    else:
        stage_list = ["csim", "synth", "cosim", "export"]

    valid_stages = {"csim", "synth", "cosim", "export"}
    bad = [s for s in stage_list if s not in valid_stages]
    if bad:
        print(
            f"ERROR: invalid stage(s): {', '.join(bad)}. "
            f"Valid: {', '.join(sorted(valid_stages))}",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Build dir setup ───────────────────────────────────────────────────
    build_root.mkdir(parents=True, exist_ok=True)
    ip_pack_dir.mkdir(parents=True, exist_ok=True)

    # ── Completion detectors ──────────────────────────────────────────────
    def _is_project_initialized(module: str) -> bool:
        return (build_root / module / "solution1" / "solution1.aps").exists()

    def _is_csim_complete(module: str) -> bool:
        csim_dir = build_root / module / "solution1" / "csim"
        if not csim_dir.exists():
            return False
        log = build_root / module / "logs" / "csim.log"
        if log.exists():
            return "PASS" in log.read_text().upper()
        return True

    def _is_synth_complete(module: str) -> bool:
        report_dir = build_root / module / "solution1" / "syn" / "report"
        if not report_dir.exists():
            return False
        return any(report_dir.glob("*csynth.rpt"))

    def _is_cosim_complete(module: str) -> bool:
        mod_dir = build_root / module
        has_dir = (
            (mod_dir / "solution1" / "sim").exists()
            or (mod_dir / "solution1" / "cosim").exists()
        )
        if not has_dir:
            return False
        log = mod_dir / "logs" / "cosim.log"
        if log.exists():
            return "PASS" in log.read_text().upper()
        return False

    def _is_export_complete(module: str) -> bool:
        mod_dir = ip_pack_dir / module
        if mod_dir.is_dir() and any(mod_dir.iterdir()):
            return True
        return any(ip_pack_dir.glob(f"{module}*.zip"))

    def _is_step_complete(module: str, step: str) -> bool:
        return {
            "csim": _is_csim_complete,
            "synth": _is_synth_complete,
            "cosim": _is_cosim_complete,
            "export": _is_export_complete,
        }[step](module)

    # ── Per-module, per-stage runner ──────────────────────────────────────
    def _run_tcl(module: str, tcl_name: str, log_name: str) -> "tuple[bool, str]":
        mod_dir = build_root / module
        tcl_path = mod_dir / tcl_name
        log_dir = mod_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / log_name)

        if not tcl_path.exists():
            with open(log_path, "w") as f:
                f.write(f"ERROR: TCL script not found: {tcl_path}\n")
                f.write("Generate TCL scripts first: arc hls gen-tcl ...\n")
            return False, log_path

        with open(log_path, "w") as log_file:
            result = subprocess.run(
                [vitis_hls, "-f", tcl_name],
                cwd=str(mod_dir),
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        return result.returncode == 0, log_path

    def _run_stage_for_module(module: str, step: str) -> "tuple[str, str, bool, str]":
        tcl_map = {
            "csim": "csim.tcl",
            "synth": "synth.tcl",
            "cosim": "cosim.tcl",
            "export": "ip_export.tcl",
        }
        log_map = {
            "csim": "csim.log",
            "synth": "synth.log",
            "cosim": "cosim.log",
            "export": "ip_export.log",
        }

        if _is_step_complete(module, step):
            print(f"  [SKIP] {step:6s} already done for {module}")
            return module, step, True, ""

        if step == "export":
            if not _is_project_initialized(module):
                ok, log = _run_tcl(module, "project.tcl", "project.log")
                if not ok:
                    return module, step, False, log
            if not _is_synth_complete(module):
                ok, log = _run_tcl(module, "synth.tcl", "synth.log")
                if not ok:
                    return module, step, False, log
        else:
            if not _is_project_initialized(module):
                ok, log = _run_tcl(module, "project.tcl", "project.log")
                if not ok:
                    return module, step, False, log

        ok, log = _run_tcl(module, tcl_map[step], log_map[step])
        return module, step, ok, log

    # ── Driver ────────────────────────────────────────────────────────────
    sep = "=" * 50
    print(f"\n{sep}")
    print(f"  arc hls run")
    print(f"{sep}")
    print(f"  Modules : {', '.join(module_list)}")
    print(f"  Stages  : {', '.join(stage_list)}")
    print(f"  Jobs    : {jobs}")
    print(f"  Build   : {build_root}")
    print(f"  IPs     : {ip_pack_dir}")
    print(f"{sep}\n")

    overall_ok = True
    for step in stage_list:
        print(f"--- Stage: {step} ---")
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(_run_stage_for_module, m, step): m for m in module_list}
            for fut in concurrent.futures.as_completed(futs):
                module, _step, ok, log_path = fut.result()
                status = "OK  " if ok else "FAIL"
                log_hint = f"  (log: {log_path})" if log_path and not ok else ""
                print(f"  [{status}] {module}{log_hint}")
                results.append((module, ok))

        failed = [m for m, ok in results if not ok]
        if failed:
            print(f"  Stage {step}: {len(failed)} module(s) FAILED: {', '.join(failed)}")
            overall_ok = False
            break
        print(f"  Stage {step}: all {len(module_list)} module(s) OK\n")

    if overall_ok:
        print("arc hls run: all stages completed successfully.")
    else:
        print("arc hls run: completed with failures.", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def register(sub) -> None:
    """Register the ``arc hls`` subparser and its commands."""
    p_hls = sub.add_parser("hls", help="Build HLS modules and IPs")
    hls_sub = p_hls.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # arc hls gen-tcl
    p_hls_gen = hls_sub.add_parser(
        "gen-tcl", help="Generate Vitis HLS TCL scripts from a plugin catalog",
    )
    p_hls_gen.add_argument(
        "module", nargs="?", default="all",
        help="Module name or 'all' (default: all)",
    )
    p_hls_gen.add_argument(
        "--hls-config", required=True, dest="hls_config", type=Path,
        help="Plugin HLS catalog YAML file",
    )
    p_hls_gen.add_argument(
        "--output-dir", dest="output_dir", type=Path, default=Path("build_hls"),
        help="Output directory for generated TCL (default: build_hls/)",
    )
    p_hls_gen.add_argument(
        "--ip-packages-dir", dest="ip_packages_dir", type=Path,
        help="Directory for exported IP archives",
    )
    p_hls_gen.add_argument(
        "--template-dir", dest="template_dir", type=Path,
        help="Override .tcl.j2 template directory",
    )
    p_hls_gen.add_argument(
        "--flow", choices=["export", "syn", "impl"], default="impl",
        help="IP export flow: export (no synth), syn (RTL only), impl (synth+P&R) (default: impl)",
    )
    p_hls_gen.set_defaults(func=cmd_hls_gen_tcl)

    # arc hls run
    p_hls_run = hls_sub.add_parser(
        "run", help="Run parallel Vitis HLS jobs (pure Python, no shell script)",
    )
    p_hls_run.add_argument(
        "--registry", dest="registry", type=Path,
        help="Plugin HLS catalog YAML file (modules registry)",
    )
    p_hls_run.add_argument(
        "--stages", dest="stages",
        help="Comma-separated HLS stages to run: csim,synth,cosim,export (default: all four)",
    )
    p_hls_run.add_argument(
        "--stage", "-c", dest="stage", choices=["csim", "synth", "cosim", "export"],
        help="Single HLS stage (legacy; use --stages for multiple)",
    )
    p_hls_run.add_argument(
        "--modules", "-m", dest="modules",
        help="Comma- or space-separated module names, or 'all' (default: all)",
    )
    p_hls_run.add_argument(
        "--jobs", "-j", type=int, default=1, dest="jobs",
        help="Maximum parallel jobs per stage (default: 1)",
    )
    p_hls_run.add_argument(
        "--hls-build-root", "-b", dest="hls_build_root", type=Path,
        help="HLS build root directory (default: build_hls/)",
    )
    p_hls_run.add_argument(
        "--ip-packages-dir", "-i", dest="ip_packages_dir", type=Path,
        help="IP packages output directory (default: <hls-build-root>/ip_packages/)",
    )
    p_hls_run.add_argument(
        "--vitis-hls", "-v", dest="vitis_hls", type=Path,
        help="Path to vitis_hls executable (default: vitis_hls on PATH)",
    )
    p_hls_run.set_defaults(func=cmd_hls_run)
