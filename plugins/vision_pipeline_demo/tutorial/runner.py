#!/usr/bin/env python3
"""vision_pipeline_demo's progressive tutorial runner.

Reads ``tutorial.yml`` (the single source of truth for which real
design/flows belong to which tutorial step) and drives the exact same
``forge`` CLI sequence ``run_vision_pipeline_demo.sh`` always has —
clean -> validate -> HLS build -> gen-top -> verify generate -> stimulus
-> doctor -> verify run — but scoped to just the requested step(s) when
one is given, instead of always running all eight designs and nine flows.

``run_vision_pipeline_demo.sh`` is now a thin wrapper around this module
(``python3 -m tutorial.runner "$@"`` from the plugin root); the module is
also directly testable without invoking any real toolchain by calling
:func:`build_plan` alone (see
forge/verify/tools/tests/test_tutorial_manifest.py).
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import yaml

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PLUGIN_ROOT.parents[1]
_FORGE_ROOT = _PLUGIN_ROOT / "forge"
_DESIGNS_DIR = _FORGE_ROOT / "designs"
_MODULES_YML = _FORGE_ROOT / "modules.yml"
_VERIFY_YML = _FORGE_ROOT / "verify" / "design.verification.yml"
_TOOLS_DIR = _FORGE_ROOT / "verify" / "tools"
_MANIFEST_PATH = _PLUGIN_ROOT / "tutorial.yml"

_TOOL_ON_PATH = {
    "vitis_hls": "vitis_hls",
    "vivado_xsim": "xsim",
}


# ── Manifest loading ─────────────────────────────────────────────────────

def load_manifest(path: Path = _MANIFEST_PATH) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    if data.get("schema_version") != 1:
        raise ValueError(f"unsupported tutorial.yml schema_version: {data.get('schema_version')!r}")
    return data


def step_by_id(manifest: dict, step_id: str) -> dict:
    for step in manifest["steps"]:
        if step["id"] == step_id:
            return step
    known = ", ".join(s["id"] for s in manifest["steps"])
    raise KeyError(f"unknown step {step_id!r} (known steps: {known})")


# ── Execution plan (pure data — no subprocess calls here, so this half is
#    unit-testable without any real toolchain) ───────────────────────────

@dataclasses.dataclass
class Plan:
    steps: list          # manifest step dicts, in manifest order
    hls_modules: list    # union of hls_modules across `steps`, dedup, order-stable
    needs_csim: bool     # pixel_normalizer_csim flow is among the selected flows
    full_selection: bool # True iff `steps` == every step in the manifest (drives
                          # whether verify generate is called globally, matching
                          # the original script's own single-call behavior exactly)


def build_plan(manifest: dict, step_ids: Optional[list] = None) -> Plan:
    """Resolve a step-id selection (``None``/empty == every step, matching
    the script's own no-argument default) into an ordered :class:`Plan`.
    """
    all_steps = manifest["steps"]
    if not step_ids:
        selected = list(all_steps)
    else:
        selected = [step_by_id(manifest, sid) for sid in step_ids]

    hls_modules: list = []
    needs_csim = False
    for step in selected:
        for m in step.get("hls_modules", []):
            if m not in hls_modules:
                hls_modules.append(m)
        for flow in step.get("flows", []):
            if flow["name"] == "pixel_normalizer_csim":
                needs_csim = True

    return Plan(
        steps=selected,
        hls_modules=hls_modules,
        needs_csim=needs_csim,
        full_selection=(len(selected) == len(all_steps)),
    )


def missing_tools(plan: Plan) -> list:
    """Real, PATH-checked missing-tool list across every requirement any
    selected step declares — checked once up front so a step fails fast
    with one clear message instead of partway through `forge hls run`.
    """
    needed = set()
    for step in plan.steps:
        needed.update(step.get("requirements", []))
    missing = []
    for req in sorted(needed):
        binary = _TOOL_ON_PATH.get(req)
        if binary and shutil.which(binary) is None:
            missing.append((req, binary))
    return missing


# ── Execution (real subprocess calls) ────────────────────────────────────

class StepFailure(RuntimeError):
    pass


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=_REPO_ROOT, **kwargs)


def _clean(plan: Plan) -> None:
    for step in plan.steps:
        if step["kind"] == "design":
            shutil.rmtree(_REPO_ROOT / "gen-top" / step["gen_top_outdir"], ignore_errors=True)
        for flow in step.get("flows", []):
            shutil.rmtree(_FORGE_ROOT / "verify" / flow["name"], ignore_errors=True)
    shutil.rmtree(_DESIGNS_DIR / "build", ignore_errors=True)
    shutil.rmtree(_DESIGNS_DIR / "ips", ignore_errors=True)


def _validate(plan: Plan) -> None:
    _run(["forge", "topgen", "validate-registry", str(_MODULES_YML)], check=True)
    for step in plan.steps:
        design_path = _DESIGNS_DIR / step["design"]
        _run(["forge", "topgen", "validate", str(design_path)], check=True)


def _hls_build(plan: Plan, hls_build_root: Path, jobs: int) -> None:
    if not plan.hls_modules and not plan.needs_csim:
        print("  (no step in this run uses an HLS module — skipping HLS build)")
        return
    _run(["forge", "hls", "gen-tcl", "--hls-config", str(_MODULES_YML), "--output-dir", str(hls_build_root)], check=True)
    if plan.needs_csim:
        _run([
            "forge", "hls", "run",
            "--registry", str(_MODULES_YML),
            "--hls-build-root", str(hls_build_root),
            "--stages", "csim",
            "--jobs", str(jobs),
            "--modules", "pixel_normalizer",
        ], check=True)
    if plan.hls_modules:
        _run([
            "forge", "hls", "run",
            "--registry", str(_MODULES_YML),
            "--hls-build-root", str(hls_build_root),
            "--stages", "synth",
            "--jobs", str(jobs),
            "--modules", ",".join(plan.hls_modules),
        ], check=True)


def _negative_gen_top_reject(step: dict, hls_build_root: Path) -> None:
    design_path = _DESIGNS_DIR / step["design"]
    tmp_out = Path("/tmp") / f"vpd_{step['id']}_rejected.v"
    tmp_log = Path("/tmp") / f"vpd_{step['id']}_gen_top.log"
    with open(tmp_log, "w") as log:
        result = _run([
            "forge", "topgen", "gen-top", str(design_path),
            "--mode", "verilog",
            "--consumer-root", ".",
            "--contracts-from", str(_MODULES_YML),
            "--hls-build-root", str(hls_build_root),
            "--output", str(tmp_out),
            "--strict",
        ], stdout=log, stderr=subprocess.STDOUT)
    tmp_out.unlink(missing_ok=True)
    if result.returncode == 0:
        raise StepFailure(
            f"{step['design']} unexpectedly PASSED gen-top --strict "
            "(should reject the undeclared CDC crossing with ATG023/ATG024)"
        )
    print(f"  {step['design']} correctly rejected by gen-top --strict (see {tmp_log})")


def _gen_top(plan: Plan, hls_build_root: Path) -> None:
    for step in plan.steps:
        if step["kind"] != "design":
            continue
        design_path = _DESIGNS_DIR / step["design"]
        out = _REPO_ROOT / "gen-top" / step["gen_top_outdir"] / "algo_top.v"
        _run([
            "forge", "topgen", "gen-top", str(design_path),
            "--mode", "verilog",
            "--consumer-root", ".",
            "--contracts-from", str(_MODULES_YML),
            "--hls-build-root", str(hls_build_root),
            "--output", str(out),
        ], check=True)


def _verify_generate(plan: Plan) -> None:
    if plan.full_selection:
        # Exactly the original script's own single, unscoped call.
        _run(["forge", "verify", "generate", str(_VERIFY_YML)], check=True)
        return
    for step in plan.steps:
        for flow in step.get("flows", []):
            _run(["forge", "verify", "generate", str(_VERIFY_YML), "--flow", flow["name"]], check=True)


def _gen_stimulus(plan: Plan) -> None:
    for step in plan.steps:
        for flow in step.get("flows", []):
            cmd = flow.get("gen_stimulus")
            if not cmd:
                continue
            parts = cmd.split()
            script, args = parts[0], parts[1:]
            _run([sys.executable, str(_TOOLS_DIR / script), *args], check=True)


def _doctor() -> None:
    _run(["forge", "verify", "doctor", str(_VERIFY_YML)], check=True)


@dataclasses.dataclass
class FlowOutcome:
    flow: str
    expected_fail: bool
    passed: bool

    @property
    def as_expected(self) -> bool:
        return self.passed != self.expected_fail


def _verify_run(plan: Plan, hls_build_root: Path) -> list:
    env = dict(os.environ)
    env["CSIM_TB_TB_PIXEL_NORMALIZER"] = str(hls_build_root / "pixel_normalizer" / "solution1" / "csim" / "build" / "csim.exe")

    outcomes = []
    for step in plan.steps:
        for flow in step.get("flows", []):
            flow_yml = _FORGE_ROOT / "verify" / flow["name"] / "verify.flow.yml"
            result = _run([
                "forge", "verify", "run", str(flow_yml),
                "--plugin", "vision_pipeline_demo",
                "--consumer-root", str(_REPO_ROOT),
            ], env=env)
            outcomes.append(FlowOutcome(
                flow=flow["name"],
                expected_fail=bool(flow.get("expect_fail", False)),
                passed=(result.returncode == 0),
            ))
    return outcomes


# ── Summary ────────────────────────────────────────────────────────────

_NEXT_PAGE_FOR_CHAPTER_PREFIX = {
    "01": "docs/tutorials/vision-pipeline-quickstart.md",
}
_DEFAULT_NEXT_PAGE = "docs/tutorials/vision-pipeline-full-design.md"


def _print_summary(plan: Plan, outcomes: list, elapsed_s: float, negative_topgen_ok: list) -> bool:
    print()
    print("== Summary ==")
    print(f"Elapsed: {elapsed_s:.1f}s")
    for step in plan.steps:
        design_path = _DESIGNS_DIR / step["design"]
        print(f"  step {step['id']} ({step['chapter']}): {design_path.relative_to(_REPO_ROOT)}")
        if step["kind"] == "design":
            print(f"    generated (FORGE):   gen-top/{step['gen_top_outdir']}/algo_top.v")
        for flow in step.get("flows", []):
            print(f"    verification (this plugin): plugins/vision_pipeline_demo/forge/verify/{flow['name']}/")
        page = _NEXT_PAGE_FOR_CHAPTER_PREFIX.get(step["chapter"], _DEFAULT_NEXT_PAGE)
        print(f"    next: {page}")

    all_ok = True
    for outcome in outcomes:
        if outcome.as_expected:
            label = "PASSED" if outcome.passed else "FAILED as expected (negative fixture evidence)"
            print(f"  v  {outcome.flow} {label}")
        else:
            all_ok = False
            if outcome.expected_fail:
                print(f"  x  {outcome.flow} PASSED but was expected to FAIL (invalid fixture regression)")
            else:
                print(f"  x  {outcome.flow} FAILED")
    for step_id, ok in negative_topgen_ok:
        if ok:
            print(f"  v  {step_id} rejected by gen-top --strict as expected")
        else:
            all_ok = False

    print()
    if all_ok:
        print(f"All {len(outcomes) + len(negative_topgen_ok)} flow(s)/check(s) behaved as expected.")
    else:
        print("One or more flows/checks behaved unexpectedly.", file=sys.stderr)
    return all_ok


# ── CLI ───────────────────────────────────────────────────────────────────

def _print_list(manifest: dict) -> None:
    print(f"{'id':<22} {'chapter':<8} title")
    for step in manifest["steps"]:
        print(f"{step['id']:<22} {step['chapter']:<8} {step['title']}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_vision_pipeline_demo.sh",
        description="Run vision_pipeline_demo's tutorial steps, progressively or all at once.",
    )
    parser.add_argument("--list", action="store_true", help="List every tutorial step and exit.")
    parser.add_argument("--step", action="append", default=[], metavar="ID",
                         help="Run only this step (repeatable). Default: every step.")
    parser.add_argument("--all", action="store_true", help="Run every step (the default with no --step given).")
    parser.add_argument("--skip-hls", action="store_true", help="Skip HLS TCL generation + csim + synth.")
    parser.add_argument("--no-clean", action="store_true", help="Skip pre-run artifact cleanup.")
    parser.add_argument("--jobs", type=int, default=4, help="Parallel HLS jobs (default: 4).")
    args = parser.parse_args(argv)

    manifest = load_manifest()

    if args.list:
        _print_list(manifest)
        return 0

    if args.step and args.all:
        print("--step and --all are mutually exclusive", file=sys.stderr)
        return 1

    step_ids = args.step or None  # None == every step, matching bare invocation
    plan = build_plan(manifest, step_ids)

    missing = missing_tools(plan)
    if missing:
        for req, binary in missing:
            print(f"missing required tool for this selection: {binary} (needed for {req})", file=sys.stderr)
        return 1

    hls_build_root = _REPO_ROOT / "build_hls_vision_pipeline_demo"
    start = time.time()

    if not args.no_clean:
        print("== Clean stale artifacts ==")
        _clean(plan)
        if not args.skip_hls and plan.full_selection:
            shutil.rmtree(hls_build_root, ignore_errors=True)

    print("== Validate ==")
    _validate(plan)

    print("== HLS build ==")
    if args.skip_hls:
        print("  (--skip-hls: skipping HLS TCL generation + csim + synth)")
    else:
        _hls_build(plan, hls_build_root, args.jobs)

    negative_topgen_ok = []
    for step in plan.steps:
        if step["kind"] == "topgen_reject":
            print(f"== Negative fixture: {step['id']} (expected to FAIL gen-top --strict) ==")
            try:
                _negative_gen_top_reject(step, hls_build_root)
                negative_topgen_ok.append((step["id"], True))
            except StepFailure as e:
                print(f"  x  {e}", file=sys.stderr)
                negative_topgen_ok.append((step["id"], False))

    print("== Generate algo_top ==")
    _gen_top(plan, hls_build_root)

    print("== forge verify generate ==")
    _verify_generate(plan)

    print("== Generate stimulus ==")
    _gen_stimulus(plan)

    print("== forge verify doctor ==")
    _doctor()

    print("== forge verify run ==")
    outcomes = _verify_run(plan, hls_build_root)

    ok = _print_summary(plan, outcomes, time.time() - start, negative_topgen_ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
