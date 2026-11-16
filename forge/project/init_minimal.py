"""``forge init`` without a plugin name: one ``forge.yml`` for the firmware a
repository already has.

It names the top, its clock and reset, where the sources are described
(a Hog project, a ``.f`` compile list, else directory globs) and, when given,
the reference data; nothing else is written and nothing in the repository is
moved. What cannot be told from the repository is returned as a question for
the user, not guessed: two equally likely tops, no clock, no reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class MinimalProject:
    config: Any                       # forge.project.config.ForgeConfig
    found: List[str] = field(default_factory=list)       # what was read from the repository
    missing: List[str] = field(default_factory=list)     # what the user still has to say, with how


def _hog_projects(root: Path) -> List[str]:
    return sorted(p.parent.name for p in (root / "Top").glob("*/list") if p.is_dir())


def _filelists(root: Path) -> List[Path]:
    skip = {".git", ".forge", "node_modules"}
    return sorted(p for p in root.rglob("*.f") if not skip & set(p.relative_to(root).parts) and p.is_file())


def minimal_project(root: Path, *, top: Optional[str] = None, clock: Optional[str] = None,
                    reset: Optional[str] = None, input: Optional[str] = None,
                    expected: Optional[str] = None, hog_project: Optional[str] = None,
                    filelist: Optional[str] = None) -> MinimalProject:
    """The ``forge.yml`` of the existing firmware under *root*."""
    from forge.project.config import ClockSpec, ForgeConfig, ResetSpec
    from forge.project.discovery import ProjectDiscovery

    root = root.resolve()
    result = ProjectDiscovery().scan(root)
    out = MinimalProject(ForgeConfig(project=root.name, root=root))
    spec: Dict[str, Any] = {"target": "existing"}

    hog = _hog_projects(root)
    lists = _filelists(root)
    if hog_project or (len(hog) == 1 and not filelist):
        name = hog_project or hog[0]
        if name not in hog:
            raise ValueError(f"no Hog project {name!r} under {root / 'Top'} (have {', '.join(hog) or 'none'})")
        spec["sources"] = {"provider": "hog", "project": name}
        out.found.append(f"sources: Hog project {name} (Top/{name}/list)")
    elif filelist or len(lists) == 1:
        chosen = filelist or str(lists[0].relative_to(root))
        spec["sources"] = {"provider": "filelist", "path": chosen}
        out.found.append(f"sources: compile list {chosen}")
    else:
        from forge.project.adopt import _source_globs

        out.config.rtl_globs = _source_globs(root, result, "rtl")
        out.found.append(f"sources: {', '.join(out.config.rtl_globs) or 'none found'}")
        if len(hog) > 1:
            out.missing.append(f"several Hog projects ({', '.join(hog)}): pick one with --hog-project")
        elif len(lists) > 1:
            out.missing.append(f"several compile lists ({', '.join(str(p.relative_to(root)) for p in lists[:4])}): "
                               "pick one with --filelist")
        if not out.config.rtl_globs:
            out.missing.append("no RTL found: give --filelist, --hog-project or sources.rtl in forge.yml")

    candidates = list(result.top_candidates)
    if top is None:
        structural = getattr(result, "structural_top", None)
        if structural:
            top = structural
        elif len(candidates) == 1:
            top = candidates[0]
    if top is None:
        out.missing.append("which module is the top: " + (
            f"one of {', '.join(candidates[:5])}" if candidates else "none was found") + "; give --top")
    else:
        out.config.top = top
        out.found.append(f"top: {top}")

    clock = clock or (result.clocks[0].name if len(result.clocks) == 1 else None)
    if clock is None:
        out.missing.append("the clock port" + (f" (one of {', '.join(c.name for c in result.clocks)})"
                                               if result.clocks else "") + ": give --clock")
    else:
        out.config.clock = ClockSpec(port=clock)
        out.found.append(f"clock: {clock}")
    found_reset = result.resets[0] if len(result.resets) == 1 else None
    if reset is None and found_reset is not None:
        reset = found_reset.name
    if reset is None:
        out.missing.append("the reset port" + (f" (one of {', '.join(r.name for r in result.resets)})"
                                               if result.resets else "") + ": give --reset")
    else:
        from forge.core.utils.signal_names import is_active_low_reset

        low = found_reset.active_low if found_reset is not None and found_reset.name == reset else None
        active = "low" if (low if low is not None else is_active_low_reset(reset)) else "high"
        out.config.reset = ResetSpec(port=reset, active=active)
        out.found.append(f"reset: {reset} (active {active})")

    for key, value in (("input", input), ("expected", expected)):
        if value:
            spec[key] = value
    if not expected:
        out.missing.append("the output the firmware should produce (verification.expected, e.g. an emulator "
                           "export): without it `forge test` simulates and reports NOT_VERIFIED_NO_ORACLE")
    out.config.verification_spec = spec
    return out
