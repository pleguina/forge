"""Staleness checks for generated/mirrored documentation.

Three independent kinds of drift this guards against:

1. **Generated reference pages** (`docs/reference/*.md`, except
   `support-classification.md`, which is migrated/curated, not generated)
   must match what `python -m forge.docsgen` would produce right now —
   checked via its own real `--check` mode, not a re-implementation.
2. **Root-file mirror pages** (`docs/development/{contributing,migration,
   security,code-of-conduct}.md`) must stay byte-synchronized with their
   root source (after their generated-header line), since MkDocs can't
   link to files outside `docs_dir` — these mirrors exist so those links
   resolve, and a mirror that silently drifts from its root would mislead
   readers of the published site.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_REFERENCE_DIR = REPO_ROOT / "docs" / "reference"

_ROOT_MIRRORS = {
    "CONTRIBUTING.md": "docs/development/contributing.md",
    "MIGRATION.md": "docs/development/migration.md",
    "SECURITY.md": "docs/development/security.md",
    "CODE_OF_CONDUCT.md": "docs/development/code-of-conduct.md",
}


def test_generated_reference_pages_are_up_to_date() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "forge.docsgen", "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "Generated reference pages are stale — run `python -m forge.docsgen`:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_root_file_mirrors_are_byte_synchronized() -> None:
    problems: "list[str]" = []
    for root_name, mirror_rel in _ROOT_MIRRORS.items():
        root_text = (REPO_ROOT / root_name).read_text(encoding="utf-8")
        mirror_path = REPO_ROOT / mirror_rel
        mirror_lines = mirror_path.read_text(encoding="utf-8").splitlines(keepends=True)

        # First two lines are the generated-header comment + a blank line
        # (see the mirrors' own `<!-- Generated from /X.md. Do not edit
        # directly. -->` header); everything after must match root exactly.
        if len(mirror_lines) < 2 or "Generated from" not in mirror_lines[0]:
            problems.append(f"{mirror_rel}: missing expected generated-header line")
            continue
        mirror_body = "".join(mirror_lines[2:])

        if mirror_body != root_text:
            problems.append(
                f"{mirror_rel} has drifted from {root_name} — "
                f"regenerate the mirror body from the current root file"
            )

    assert problems == [], "\n".join(problems)


def test_root_file_mirrors_reference_the_correct_source() -> None:
    for root_name, mirror_rel in _ROOT_MIRRORS.items():
        header = (REPO_ROOT / mirror_rel).read_text(encoding="utf-8").splitlines()[0]
        assert f"/{root_name}" in header, f"{mirror_rel}'s header doesn't cite {root_name}"


# ─────────────────────────────────────────────────────────────────────────────
# Hand-written overview pages
# ─────────────────────────────────────────────────────────────────────────────

CAPABILITIES = REPO_ROOT / "docs" / "capabilities.md"

#: Groups whose capability is described under a different heading than their
#: own name. `core` holds shared utilities surfaced as `verify-contract`
#: and `resources` rather than as a "core" capability of its own.
_CAPABILITY_ALIASES = {"core": "verify-contract"}


def test_capabilities_page_mentions_every_cli_command_group() -> None:
    """`docs/capabilities.md` is the one page that claims to cover what the
    framework does, so a command group it never mentions is a real gap —
    exactly how `forge contract` and the golden-path verbs went unmentioned
    in the README for a whole release cycle.
    """
    from argparse import _SubParsersAction

    from forge.core.cli.main import build_parser

    parser = build_parser()
    groups = next(
        (a.choices for a in parser._actions if isinstance(a, _SubParsersAction)), {}
    )
    assert groups, "could not enumerate CLI groups"

    text = CAPABILITIES.read_text(encoding="utf-8")
    missing = [
        name for name in groups
        if _CAPABILITY_ALIASES.get(name, name) not in text
    ]

    assert not missing, (
        "docs/capabilities.md does not mention these CLI command groups: "
        f"{sorted(missing)}. Add them, or alias them in _CAPABILITY_ALIASES "
        "if they are covered under a different name."
    )


_SCOPE_START = "| Axis | Current support |"
_SCOPE_END = "| Vendors/toolchains **not** supported |"


def _scope_table(path: Path) -> str:
    """The shared support-scope table, normalised for comparison.

    Both files carry it: the README because GitHub renders it, and
    docs/index.md because MkDocs cannot include a file outside docs_dir.
    Link syntax legitimately differs between the two (GitHub needs
    `docs/`-prefixed paths), so compare the prose with links stripped.
    """
    text = path.read_text(encoding="utf-8")
    start = text.index(_SCOPE_START)
    end = text.index("\n", text.index(_SCOPE_END, start))
    block = text[start:end]
    block = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", block)   # links -> their text
    block = re.sub(r"`[^`]*`", "", block)                    # code spans (paths differ)
    return re.sub(r"\s+", " ", block).strip()


def test_support_scope_table_matches_between_readme_and_docs_index() -> None:
    """The table is duplicated because each renderer needs its own copy.
    Duplication without a check is how the two silently disagree about what
    the project supports."""
    readme = _scope_table(REPO_ROOT / "README.md")
    index = _scope_table(REPO_ROOT / "docs" / "index.md")

    assert readme == index, (
        "The 'Current scope' table differs between README.md and "
        "docs/index.md. They must state the same support scope."
    )
