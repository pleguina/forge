"""Regression guard for CLI help/hint drift.

`ci/stale_reference_check.sh` greps for known-dead patterns (fw_verify,
bare topgen, etc.) but never asserted that `forge --help` / subcommand
`--help` text actually matches the live argparse tree. That gap let the
`framework` group go missing from the top-level epilog and `--group` help
without anything failing. These tests walk the *real* parser built by
`build_parser()` and cross-check it against the hand-written hint text,
so any future rename/removal that isn't mirrored in the help strings
fails here instead of being discovered by a user.
"""

from __future__ import annotations

import re
import subprocess
import sys
from argparse import ArgumentParser, _SubParsersAction

import pytest

from forge.core.cli.main import build_parser
from forge.verify.__main__ import build_parser as build_verify_parser

# `forge verify` doesn't build its sub-commands directly under the top-level
# parser: it swallows everything after "verify" into a REMAINDER positional
# and hands off to forge.verify.__main__'s own parser (see
# core/cli/groups/verify.py::cmd_verify_dispatch). Use the real delegate
# parser when validating "verify" examples/flags.
_DELEGATED_GROUP_PARSERS = {"verify": build_verify_parser}


def _subparsers_action(parser: ArgumentParser) -> _SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, _SubParsersAction):
            return action
    return None


def _iter_command_tree(parser: ArgumentParser, prefix: tuple[str, ...] = ()):
    """Yield (path, parser) for every leaf and intermediate subcommand."""
    yield prefix, parser
    action = _subparsers_action(parser)
    if action is None:
        return
    for name, sub in action.choices.items():
        yield from _iter_command_tree(sub, prefix + (name,))


def _all_option_strings(parser: ArgumentParser) -> set[str]:
    strings: set[str] = set()
    for action in parser._actions:
        strings.update(action.option_strings)
    return strings


def test_epilog_groups_match_registered_groups() -> None:
    parser = build_parser()
    group_action = _subparsers_action(parser)
    assert group_action is not None
    registered = set(group_action.choices)

    epilog = parser.epilog or ""
    groups_block = epilog.split("Examples:")[0]
    documented = set(re.findall(r"^\s{2}(\S+)", groups_block, flags=re.MULTILINE))

    assert documented == registered, (
        f"epilog 'Groups:' list is out of sync with registered CLI groups.\n"
        f"documented={documented} registered={registered}"
    )


def test_group_help_text_mentions_every_registered_group() -> None:
    parser = build_parser()
    group_action = _subparsers_action(parser)
    assert group_action is not None

    help_text = group_action.help or ""
    for name in group_action.choices:
        assert name in help_text, (
            f"group {name!r} is registered but not mentioned in the "
            f"--group help string: {help_text!r}"
        )


def test_epilog_examples_use_real_groups_subcommands_and_flags() -> None:
    parser = build_parser()
    group_action = _subparsers_action(parser)
    assert group_action is not None

    epilog = parser.epilog or ""
    examples = epilog.split("Examples:")[1]
    example_lines = [
        line.strip() for line in examples.strip().splitlines() if line.strip()
    ]
    assert example_lines, "expected at least one example command in the epilog"

    for line in example_lines:
        assert line.startswith("forge "), f"example line does not start with 'forge': {line!r}"
        tokens = line.split()
        group_name = tokens[1]
        assert group_name in group_action.choices, (
            f"example references unknown group {group_name!r}: {line!r}"
        )
        group_parser = group_action.choices[group_name]
        if group_name in _DELEGATED_GROUP_PARSERS:
            group_parser = _DELEGATED_GROUP_PARSERS[group_name]()

        cmd_action = _subparsers_action(group_parser)
        target_parser = group_parser
        rest = tokens[2:]
        if cmd_action is not None:
            assert rest, f"example for group {group_name!r} has no subcommand: {line!r}"
            subcmd_name = rest[0]
            assert subcmd_name in cmd_action.choices, (
                f"example references unknown {group_name} subcommand "
                f"{subcmd_name!r}: {line!r}"
            )
            target_parser = cmd_action.choices[subcmd_name]
            rest = rest[1:]

        valid_flags = _all_option_strings(target_parser)
        for token in rest:
            if token.startswith("--"):
                flag = token.split("=", 1)[0]
                assert flag in valid_flags, (
                    f"example uses flag {flag!r} not defined on "
                    f"'{line.split(flag)[0].strip()}': {line!r}\n"
                    f"valid flags: {sorted(valid_flags)}"
                )


@pytest.mark.parametrize(
    "path",
    [p for p, _ in _iter_command_tree(build_parser())],
    ids=lambda p: " ".join(p) or "forge",
)
def test_help_runs_cleanly_for_every_command(path: tuple[str, ...]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "forge.core.cli.main", *path, "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"`forge {' '.join(path)} --help` failed:\n{result.stderr}"
    )
    assert "usage:" in result.stdout.lower()
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "subcmd", list(_subparsers_action(build_verify_parser()).choices)
)
def test_help_runs_cleanly_for_every_verify_subcommand(subcmd: str) -> None:
    """`forge verify <subcmd> --help` is delegated via REMAINDER, so it's
    invisible to test_help_runs_cleanly_for_every_command's parser walk —
    exercise it explicitly."""
    result = subprocess.run(
        [sys.executable, "-m", "forge.core.cli.main", "verify", subcmd, "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"`forge verify {subcmd} --help` failed:\n{result.stderr}"
    )
    assert "usage:" in result.stdout.lower()
    assert "Traceback" not in result.stderr
