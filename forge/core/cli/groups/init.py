"""forge init — a ``forge.yml`` for the firmware a repository already has
(:mod:`forge.project.init_minimal`), or, with ``--example <plugin_id>``, the
example plugin for extension authors.

The example unifies the two plugin scaffolders and chains the whole golden
path: `forge init --example <plugin_id>` runs, in order, the topgen-side scaffolder, the (now-fixed) verify-side
scaffolder, `forge dev topgen validate`, `forge build`, `forge test
prepare`/`run`, and `forge report` — real invocations of those real
commands (constructed as argv and run in-process through the full `forge`
parser, exactly as a user typing each command would get), not
reimplementations.

Any step failing halts immediately with a clear diagnostic naming which
step failed — never silently continuing past a failure and claiming
success (governing rule 11).

This command depends on the verify-side scaffolder templates in
`forge/verification/plugin_scaffold.py` matching the scaffolded RTL (an exact
copy of `plugins/passthrough_demo`'s working RTL): both halves are real
and matched, so the whole chain runs with zero manual edits.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List


def cmd_init_minimal(args) -> None:
    """``forge init`` without a plugin name: a ``forge.yml`` for existing firmware."""
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.project.init_minimal import minimal_project
    from forge.project.paths import ROOT_CONFIG_NAME

    json_mode = getattr(args, "json", False)
    root = Path(getattr(args, "consumer_root", None) or ".").expanduser().resolve()
    target = root / ROOT_CONFIG_NAME
    if target.exists() and not getattr(args, "force", False):
        sys.exit(emit(CommandEnvelope(status="fail", diagnostics=[{
            "severity": "error", "message": f"{target} already exists; edit it, or pass --force to replace it"}]),
            json_mode=json_mode))
    try:
        made = minimal_project(root, top=args.top, clock=args.clock, reset=args.reset, input=args.input,
                               expected=args.expected, hog_project=args.hog_project, filelist=args.filelist)
    except (OSError, ValueError) as exc:
        sys.exit(emit(CommandEnvelope(status="fail", diagnostics=[{"severity": "error", "message": str(exc)}]),
                      json_mode=json_mode))
    path = made.config.save(target)
    if not json_mode:
        print(f"forge init — {path}")
        for line in made.found:
            print(f"  ✅ {line}")
        for line in made.missing:
            print(f"  ❓ {line}")
    blocking = [m for m in made.missing if not m.startswith("the output")]
    sys.exit(emit(CommandEnvelope(
        status="warn" if made.missing else "pass",
        diagnostics=[{"severity": "warning", "message": m} for m in made.missing],
        artifacts=[str(path)],
        metrics={"found": made.found, "missing": made.missing},
        next_actions=(["Answer the questions above (flags of `forge init --force`, or edit forge.yml)"]
                      if blocking else []) + ["`forge check`, then `forge test`"],
    ), json_mode=json_mode))


def cmd_init(args) -> None:
    from forge.core.cli._shared import run_forge_command
    from forge.core.cli.envelope import CommandEnvelope, emit

    json_mode = getattr(args, "json", False)
    if getattr(args, "name", None):
        sys.exit(emit(CommandEnvelope(status="fail", diagnostics=[{
            "severity": "error",
            "message": f"`forge init {args.name}`: forge init writes forge.yml for the firmware in this "
                       f"repository and takes no name; the example plugin is `forge init --example {args.name}`"}]),
            json_mode=json_mode))
    if not getattr(args, "plugin_id", None):
        cmd_init_minimal(args)
        return
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
    skipped_steps: List[str] = []

    # Each stage below is a full `forge` command with its own report banners
    # and "next steps" epilogue. Relaying all seven verbatim printed the same
    # design-validation report three times and recommended commands `forge
    # init` had already run. Capture them by default; `--verbose` restores
    # the full transcript.
    verbose = getattr(args, "verbose", False)
    quiet = json_mode or not verbose

    def _fail(step: str, code: int, output: str = "") -> None:
        if output and quiet:
            # The step's own output is the only useful diagnosis here, so it
            # stops being noise the moment something actually breaks.
            print(output, end="" if output.endswith("\n") else "\n", file=sys.stderr)
        envelope = CommandEnvelope(
            status="fail",
            diagnostics=[{
                "severity": "error",
                "message": f"forge init step {step!r} failed (exit {code})",
            }],
            metrics={"steps_completed": steps_completed, "failed_step": step},
        )
        sys.exit(emit(envelope, json_mode=json_mode))

    def _step(name: str, label: str, argv: List[str]) -> None:
        """Run one stage, record it, and report it as a single line."""
        code, output = run_forge_command(argv, quiet=quiet)
        if code != 0:
            _fail(name, code, output)
        steps_completed.append(name)
        if not json_mode and not verbose:
            print(f"  ✅ {label}")

    if not json_mode:
        print(f"forge init — scaffolding {plugin_id!r}")

    # ── Step 1: topgen-side scaffold ────────────────────────────────────────
    _step("topgen-init-plugin", "topology scaffold", [
        "dev", "topgen", "init-plugin", plugin_id,
        "--plugins-root", str(plugins_root), "--algo-root", str(algo_root),
        *(["--dry-run"] if dry_run else []),
    ])

    # ── Step 2: verify-side scaffold ────────────────────────────────────────
    _step("verify-init-plugin", "verification scaffold", [
        "dev", "verify", "init-plugin", plugin_id,
        "--plugins-root", str(plugins_root),
        *(["--dry-run"] if dry_run else []),
    ])

    if dry_run:
        envelope = CommandEnvelope(
            status="pass",
            metrics={"steps_completed": steps_completed, "dry_run": True},
            next_actions=[
                f"Re-run without --dry-run to write the plugin skeleton and chain "
                f"validate -> build -> test -> report for {plugin_id!r}",
            ],
        )
        sys.exit(emit(envelope, json_mode=json_mode))

    # The project file the bare commands read, unless the repository already
    # has one for another design.
    from forge.project.config import ForgeConfig
    from forge.project.paths import ROOT_CONFIG_NAME

    forge_yml = consumer_root / ROOT_CONFIG_NAME
    wrote_forge_yml = not forge_yml.exists()
    if wrote_forge_yml:
        ForgeConfig(
            project=plugin_id,
            root=consumer_root,
            design=os.path.relpath(design_yml, consumer_root),
            verification_config=os.path.relpath(design_verification_yml, consumer_root),
        ).save()
        if not json_mode:
            print(f"  ✅ {ROOT_CONFIG_NAME} written")
    elif not json_mode:
        print(f"  ⏭️  {forge_yml} already exists, left as it is")

    # ── Step 3: validate immediately ────────────────────────────────────────
    _step("validate", "design validated", ["dev", "topgen", "validate", str(design_yml)])

    # ── Step 4: build immediately (real generation) ────────────────────────
    _step("build", f"top level generated ({gen_top_output.name})", [
        "build", str(design_yml),
        "--contracts-from", str(modules_yml),
        "--consumer-root", str(consumer_root),
        "--output", str(gen_top_output),
    ])

    # ── Step 5: run at least one test ───────────────────────────────────────
    # The scaffolded flow simulates with xsim. Scaffold, validate, build and
    # report all work fine without a simulator installed, so a missing
    # toolchain skips the simulation with a note rather than failing the
    # whole command — otherwise `forge init`, the first thing a new user
    # runs, dies at stage 6 of 7 on any machine without Vivado.
    from forge.core.toolchain_versions import tool_present

    simulator_available = all(tool_present(t) for t in ("xvlog", "xelab", "xsim"))
    if not simulator_available:
        if not json_mode:
            print("  ⏭️  simulation skipped — xsim not on PATH (run `forge doctor`)")
        skipped_steps.append("test-run")

    if simulator_available:
        _step("test-prepare", "testbench prepared", [
            "test", "prepare", str(design_verification_yml),
            "--flow", flow_name, "--consumer-root", str(consumer_root),
        ])

        _step("test-run", f"simulation passed ({flow_name})", [
            "test", "run", str(design_verification_yml),
            "--flow", flow_name, "--plugin", plugin_id,
            "--consumer-root", str(consumer_root),
            "--event-id", "0", "--junit-xml", str(junit_path),
        ])

    # ── Step 6: generate a report ────────────────────────────────────────────
    _step("report", "report written", [
        "report", str(design_yml),
        "--contracts-from", str(modules_yml),
        "--output", str(report_dir),
        *(["--junit-xml", str(junit_path)] if junit_path.exists() else []),
    ])

    artifacts = [
        str(p) for p in (
            design_yml, modules_yml, interface_yaml, rtl_stub,
            design_verification_yml, bootstrap_py, gen_stimulus_py, golden_xml,
            junit_path, *([forge_yml] if wrote_forge_yml else []),
        )
        if p.exists()
    ]
    if report_dir.is_dir():
        artifacts.extend(str(p) for p in sorted(report_dir.glob("*")) if p.is_file())

    envelope = CommandEnvelope(
        # Nothing has checked the generated top level when the simulation
        # did not run, so this is not a pass.
        status="warn" if skipped_steps else "pass",
        diagnostics=[{
            "severity": "warning",
            "message": "the generated top level was not simulated — xsim is not on PATH",
        }] if skipped_steps else [],
        artifacts=artifacts,
        metrics={
            "steps_completed": steps_completed,
            "steps_skipped": skipped_steps,
            "plugin_id": plugin_id,
        },
        next_actions=[
            *([
                "Install a simulator (Vivado xsim) and re-run `forge init`, or run "
                f"`forge test run {design_verification_yml} --flow {flow_name}` once "
                "one is on PATH, to simulate the design"
            ] if skipped_steps else []),
            f"Open {report_dir / 'dashboard.html'} to see the generated topology, "
            f"latency check and simulation result",
            *([
                f"From {consumer_root}, `forge check`, `forge build`, `forge test` and "
                f"`forge report` work on {plugin_id!r} with no arguments"
            ] if wrote_forge_yml else []),
            f"Replace the scaffolded RTL stub ({rtl_stub}) with your real algorithm, "
            f"then replace {golden_xml} with real golden data",
        ],
    )
    sys.exit(emit(envelope, json_mode=json_mode))


def register(sub) -> None:
    """Register the top-level ``forge init`` command."""
    p = sub.add_parser(
        "init",
        help="Write forge.yml for the firmware you have (or --example: scaffold the example plugin)",
        description="Write one forge.yml that compares the firmware already in this repository with "
                    "reference data: its top, clock, reset and source description; nothing else is written. "
                    "--example scaffolds the full example plugin for extension authors and chains "
                    "validate -> build -> test -> report.",
    )
    p.add_argument("name", nargs="?", default=None, help=argparse.SUPPRESS)
    p.add_argument("--example", dest="plugin_id", default=None, metavar="PLUGIN_ID",
                   help="Scaffold the example plugin PLUGIN_ID (topology, contracts, RTL stub, verification flow) "
                        "and chain validate -> build -> test -> report, for extension authors")
    existing = p.add_argument_group("existing firmware")
    existing.add_argument("--top", default=None, help="The top-level module (default: the one discovery finds)")
    existing.add_argument("--clock", default=None, help="Its clock port")
    existing.add_argument("--reset", default=None, help="Its reset port")
    existing.add_argument("--input", default=None, help="Input data (JSON, EMP or APx pattern file)")
    existing.add_argument("--expected", default=None, help="The output the firmware should produce")
    existing.add_argument("--hog-project", dest="hog_project", default=None,
                          help="Read the sources from this Hog project (Top/<name>/list)")
    existing.add_argument("--filelist", default=None, help="Read the sources from this .f compile list")
    existing.add_argument("--force", action="store_true", default=False, help="Replace an existing forge.yml")
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
    p.add_argument(
        "--verbose", "-v", action="store_true", default=False,
        help="Print each underlying command's full output instead of one line per stage",
    )
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p.set_defaults(func=cmd_init)
