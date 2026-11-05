"""A replay package: what a board needs to replay a payload's reference, and
what a capture from it is compared against.

``forge report --package <target>`` writes, under
``.forge/reports/packages/<target>/``, the target's input and expected output
files and their event names, the link each interface is on, how outputs are
compared, the identity of the build the reference was simulated against, and
the simulation results that used the same reference. The board's own tools
load and play the input and capture the output; ``forge test --capture
<file> --package <dir>`` compares the capture with the scoreboard simulation
uses, after checking that the reference in ``forge.yml`` is still the
package's.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PACKAGE_SCHEMA = "forge.replay_package/1"
MANIFEST = "manifest.json"


def package_dir(paths: Any, target: str) -> Path:
    return paths.report_root / "packages" / target


def _generated_dir(level: Any) -> Optional[Path]:
    return next((f.parent for f in level.sources if (f.parent / "provenance.json").is_file()), None)


def _reference(level: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("input", "expected"):
        ref = getattr(level, key)
        entry = {"file": f"data/{key}{ref.path.suffix}", "format": ref.format, "sha256": ref.sha256()}
        if ref.events is not None:
            entry["events"] = {"file": f"data/{key}.events{ref.events.suffix}",
                               "sha256": ref.to_dict()["events"]["sha256"]}
        out[key] = entry
    return out


def write_package(config: Any, target: str, out: Optional[Path] = None) -> Path:
    """Write the replay package of *target* (a design-scope level of the
    project's plan) and return its directory."""
    from forge.project.verification_state import current_plan, latest_results
    from forge.verification import auto
    from forge.verification import scoreboard as sb

    plan = current_plan(config)
    level = next((lv for lv in plan.levels if lv.name == target), None)
    if level is None:
        raise ValueError(f"no target {target!r} (have {', '.join(lv.name for lv in plan.levels)})")
    if level.scope != "design" or level.bench is None:
        raise ValueError(f"{target}: a board replays the whole payload; package a design-scope target")
    if level.input is None or level.expected is None:
        raise ValueError(f"{target}: a replay package needs the input and the expected output")
    paths = config.paths()
    out = out or package_dir(paths, target)
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    reference = _reference(level)
    for key, entry in reference.items():
        ref = getattr(level, key)
        shutil.copyfile(ref.path, out / entry["file"])
        if ref.events is not None:
            shutil.copyfile(ref.events, out / entry["events"]["file"])
    simulated = {r.name: {"status": r.status, "evidence_id": r.payload.get("evidence_id"), "stale": r.stale}
                 for r in latest_results(paths.report_root)
                 if r.payload.get("level") == target and r.payload.get("backend") != auto.BOARD}
    links = level.expected.links or level.input.links
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "project": config.project,
        "target": target,
        "top": level.top,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build": auto.build_identity(level, _generated_dir(level)),
        "reference": reference,
        "links": dict(links),
        "association": dict(level.expected.association or level.input.association),
        "compare": level.compare.to_dict(),
        "checker": sb.CHECKER_VERSION,
        "clock_period_ns": level.bench.period_ns,
        "interfaces": [{"name": i.name, "direction": i.direction, "protocol": i.version, "width": i.width,
                        "link": links.get(i.name)} for i in level.bench.interfaces],
        "simulated": simulated,
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "README.md").write_text(_readme(manifest))
    return out


def _readme(manifest: Dict[str, Any]) -> str:
    rows: List[str] = [f"| {i['name']} | {i['direction']} | {i['protocol']} | {i['width']} | "
                       f"{'' if i['link'] is None else i['link']} |" for i in manifest["interfaces"]]
    sims = ", ".join(f"{k}: {v['status']}" + (" (out of date)" if v["stale"] else "")
                     for k, v in manifest["simulated"].items()) or "none recorded"
    return "\n".join([
        f"# Replay package: {manifest['project']} / {manifest['target']}", "",
        f"Build `{manifest['build']['id'][:16]}` of `{manifest['top']}`; simulated against this reference: {sims}.", "",
        "| interface | direction | protocol | width | link |", "|---|---|---|---|---|", *rows, "",
        f"Play `{manifest['reference']['input']['file']}` with the board's tools, capture the output links, and compare:",
        "", "```bash",
        f"forge test --target {manifest['target']} --capture <capture file> --package <this directory>",
        "```", "",
        f"Outputs are compared as `{manifest['compare']['mode']}`; a latency mode also needs `--capture-offset`, "
        "the capture frame at which the input's frame 0 was played.", "",
    ])


def check_package(package: Path, level: Any) -> Dict[str, Any]:
    """The manifest of *package*, after checking it is *level*'s and that the
    reference ``forge.yml`` names is still the one it holds."""
    manifest_path = package / MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(f"{package}: no {MANIFEST}; write one with `forge report --package <target>`")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != PACKAGE_SCHEMA:
        raise ValueError(f"{manifest_path}: schema {manifest.get('schema')!r}, want {PACKAGE_SCHEMA}")
    if manifest["target"] != level.name:
        raise ValueError(f"{package} is the package of {manifest['target']}, not {level.name}")
    changed = [key for key in ("input", "expected")
               if getattr(level, key) is None or getattr(level, key).sha256() != manifest["reference"][key]["sha256"]]
    if level.expected is not None and dict(level.expected.links) != {k: v for k, v in manifest["links"].items()}:
        changed.append("links")
    if changed:
        raise ValueError(f"{level.name}: the {' and '.join(changed)} in forge.yml differ from the package's; "
                         "compare against the package's reference, or write a new package")
    return manifest
