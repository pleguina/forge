"""``python -m forge.docsgen`` — regenerate (or check) every page under
``docs/reference/`` (release-plan Phase 9, §9.3/§9.4).

Usage::

    python -m forge.docsgen              # regenerate every page
    python -m forge.docsgen --check       # exit 1 if any page is stale, write nothing
    python -m forge.docsgen --output-dir docs/reference   # default shown
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .artifact_walker import generate_artifacts_page
from .cli_reference import generate_cli_reference_page
from .diagnostics_registry import generate_diagnostics_page
from .support_matrix_reference import generate_support_matrix_page
from .vocab_reference import (
    generate_canonical_roles_page,
    generate_interface_members_page,
    generate_protocols_page,
    generate_transformations_page,
)
from ._io import write_page

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "docs" / "reference"

# (relative filename, generator) — one entry per generated reference page.
_PAGES = [
    ("cli.md", generate_cli_reference_page),
    ("canonical-roles.md", generate_canonical_roles_page),
    ("protocols.md", generate_protocols_page),
    ("interface-members.md", generate_interface_members_page),
    ("transformations.md", generate_transformations_page),
    ("diagnostics.md", generate_diagnostics_page),
    ("support-matrix.md", generate_support_matrix_page),
    ("artifacts.md", generate_artifacts_page),
]


def _parse_args(argv: "list[str] | None" = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m forge.docsgen",
        description="Regenerate FORGE's generated docs/reference/*.md pages.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Verify pages are up to date; write nothing; exit 1 if any page is stale.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR,
        help=f"Directory to write pages into (default: {_DEFAULT_OUTPUT_DIR.relative_to(_REPO_ROOT)}).",
    )
    return parser.parse_args(argv)


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv)

    stale: "list[str]" = []
    for relative_name, generator in _PAGES:
        content = generator()
        if args.check:
            existing_path = args.output_dir / relative_name
            existing = existing_path.read_text(encoding="utf-8") if existing_path.exists() else None
            if existing != content:
                stale.append(relative_name)
        else:
            write_page(args.output_dir, relative_name, content)

    if args.check:
        if stale:
            print("Stale generated reference page(s) — run `python -m forge.docsgen`:", file=sys.stderr)
            for name in stale:
                print(f"  - {args.output_dir / name}", file=sys.stderr)
            return 1
        print("All generated reference pages are up to date.")
        return 0

    print(f"Wrote {len(_PAGES)} page(s) to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
