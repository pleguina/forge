"""forge init — unify the two existing plugin scaffolders and chain the
whole golden path: `forge init <plugin_id>`
runs, in order, the topgen-side scaffolder, the (now-fixed) verify-side
scaffolder, `forge topgen validate`, `forge build --apply`, `forge test
prepare`/`run`, and `forge report` — real invocations of those real
commands (constructed as argv and run in-process through the full `forge`
parser, exactly as a user typing each command would get), not
reimplementations.

Any step failing halts immediately with a clear diagnostic naming which
step failed — never silently continuing past a failure and claiming
success (governing rule 11).

This command depends on the verify-side scaffolder templates being fixed
(this same slice, in `forge/verify/__main__.py`) — before that fix, the
scaffolded RTL (an exact copy of `plugins/passthrough_demo`'s real,
working RTL) had no matching working verify-side counterpart, so "run at
least one test" was unmeetable without manual edits. After the fix, both
halves are real and matched, so the whole chain runs with zero manual
edits — confirmed by this slice's own end-to-end test.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _run_forge_command(argv: List[str], *, quiet: bool) -> int:
    """Run a real `forge <argv>` invocation in-process through the full
    parser — the same argv a user would type, so every step here is a
    real command, not a hand-built call into internals."""
    from forge.core.cli.main import build_parser

    parser = build_parser()
    parsed = parser.parse_args(argv)
    ctx = contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext()
    with ctx:
        try:
            result = parsed.func(parsed)
        except SystemExit as exc:
            return 0 if exc.code is None else int(exc.code)
    return 0 if result is None else int(result)


def cmd_init(args) -> None:
    from forge.core.cli.envelope import CommandEnvelope, emit

    json_mode = getattr(args, "json", False)
    dry_run = getattr(args, "dry_run", False)
    plugin_id = args.plugin_id

    plugins_root = Path(args.plugins_root).expanduser().resolve()
    algo_root = Path(getattr(args, "algo_root", None) or args.plugins_root).expanduser().resolve()
    consumer_root = (
        Path(args.consumer_root).expanduser().resolve()
        if getattr(args, "consumer_root", None)
        else plugins_root.parent
    )

    forge_root = plugins_root / plugin_id / "forge"
    design_yml = forge_root / "designs" / "design.yml"
    modules_yml = forge_root / "modules.yml"
    interface_yaml = forge_root / "interfaces" / f"{plugin_id}.interface.yaml"
    rtl_stub = algo_root / plugin_id / "algo" / "rtl" / f"{plugin_id}.v"
    verify_root = forge_root / "verify"
    design_verification_yml = verify_root / "design.verification.yml"
    bootstrap_py = verify_root / "tools" / "bootstrap.py"
    gen_stimulus_py = verify_root / "tools" / "gen_stimulus.py"
    golden_xml = verify_root / "schemas" / "data" / f"{plugin_id}_golden.xml"

    flow_name = f"{plugin_id}_xsim"
    gen_top_output = consumer_root / "gen-top" / f"design_{plugin_id}" / "algo_top.v"
    junit_path = verify_root / "junit.xml"
    report_dir = plugins_root / plugin_id / "report"

    steps_completed: List[str] = []

    def _fail(step: str, code: int) -> None:
        envelope = CommandEnvelope(
            status="fail",
            diagnostics=[{
                "severity": "error",
                "message": f"forge init step {step!r} failed (exit {code})",
            }],
            metrics={"steps_completed": steps_completed, "failed_step": step},
        )
        sys.exit(emit(envelope, json_mode=json_mode))

    # ── Step 1: topgen-side scaffold ────────────────────────────────────────
    code = _run_forge_command([
        "topgen", "init-plugin", plugin_id,
        "--plugins-root", str(plugins_root), "--algo-root", str(algo_root),
        *(["--dry-run"] if dry_run else []),
    ], quiet=json_mode)
    if code != 0:
        _fail("topgen-init-plugin", code)
    steps_completed.append("topgen-init-plugin")

    # ── Step 2: verify-side scaffold ────────────────────────────────────────
    code = _run_forge_command([
        "verify", "init-plugin", plugin_id,
        "--plugins-root", str(plugins_root),
        *(["--dry-run"] if dry_run else []),
    ], quiet=json_mode)
    if code != 0:
        _fail("verify-init-plugin", code)
    steps_completed.append("verify-init-plugin")

    if dry_run:
        envelope = CommandEnvelope(
            status="pass",
            metrics={"steps_completed": steps_completed, "dry_run": True},
            next_actions=[
                f"Re-run without --dry-run to write the plugin skeleton and chain "
                f"validate -> build --apply -> test -> report for {plugin_id!r}",
            ],
        )
        sys.exit(emit(envelope, json_mode=json_mode))

    # ── Step 3: validate immediately ────────────────────────────────────────
    code = _run_forge_command(["topgen", "validate", str(design_yml)], quiet=json_mode)
    if code != 0:
        _fail("validate", code)
    steps_completed.append("validate")

    # ── Step 4: build immediately (real generation) ────────────────────────
    code = _run_forge_command([
        "build", str(design_yml),
        "--contracts-from", str(modules_yml),
        "--consumer-root", str(consumer_root),
        "--output", str(gen_top_output),
        "--apply",
    ], quiet=json_mode)
    if code != 0:
        _fail("build", code)
    steps_completed.append("build")

    # ── Step 5: run at least one test ───────────────────────────────────────
    code = _run_forge_command([
        "test", "prepare", str(design_verification_yml),
        "--flow", flow_name, "--consumer-root", str(consumer_root),
    ], quiet=json_mode)
    if code != 0:
        _fail("test-prepare", code)
    steps_completed.append("test-prepare")

    code = _run_forge_command([
        "test", "run", str(design_verification_yml),
        "--flow", flow_name, "--plugin", plugin_id,
        "--consumer-root", str(consumer_root),
        "--event-id", "0", "--junit-xml", str(junit_path),
    ], quiet=json_mode)
    if code != 0:
        _fail("test-run", code)
    steps_completed.append("test-run")

    # ── Step 6: generate a report ────────────────────────────────────────────
    code = _run_forge_command([
        "report", str(design_yml),
        "--contracts-from", str(modules_yml),
        "--output", str(report_dir),
        "--junit-xml", str(junit_path),
    ], quiet=json_mode)
    if code != 0:
        _fail("report", code)
    steps_completed.append("report")

    artifacts = [
        str(p) for p in (
            design_yml, modules_yml, interface_yaml, rtl_stub,
            design_verification_yml, bootstrap_py, gen_stimulus_py, golden_xml,
            junit_path,
        )
        if p.exists()
    ]
    if report_dir.is_dir():
        artifacts.extend(str(p) for p in sorted(report_dir.glob("*")) if p.is_file())

    envelope = CommandEnvelope(
        status="pass",
        artifacts=artifacts,
        metrics={"steps_completed": steps_completed, "plugin_id": plugin_id},
        next_actions=[
            f"Replace the scaffolded RTL stub ({rtl_stub}) with your real algorithm, "
            f"then replace {golden_xml} with real golden data",
        ],
    )
    sys.exit(emit(envelope, json_mode=json_mode))


def register(sub) -> None:
    """Register the top-level ``forge init`` command."""
    p = sub.add_parser(
        "init",
        help="Scaffold a new plugin and chain validate -> build -> test -> report",
    )
    p.add_argument("plugin_id", help="Plugin identifier (e.g. 'my_algo')")
    p.add_argument(
        "--plugins-root", default="plugins",
        help="Root directory under which to create <plugin_id>/forge/ (default: plugins/)",
    )
    p.add_argument(
        "--algo-root", default=None,
        help="Root directory under which to create <plugin_id>/algo/ (default: same as --plugins-root)",
    )
    p.add_argument(
        "--consumer-root", default=None,
        help="Repo root for path resolution (default: --plugins-root's parent directory)",
    )
    p.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Preview the scaffold only — validate/build/test/report are skipped entirely",
    )
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p.set_defaults(func=cmd_init)
