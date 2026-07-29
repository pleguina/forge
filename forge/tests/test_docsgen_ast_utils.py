"""Tests for forge.docsgen._ast_utils (release-plan Phase 9, Defect 13)."""
from __future__ import annotations

from pathlib import Path

from forge.docsgen._ast_utils import find_call_kwarg_string_values

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_finds_literal_kwarg_values(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def make():\n"
        "    return Thing(kind='alpha')\n"
        "\n"
        "def make2():\n"
        "    return Thing(kind='beta')\n"
    )
    assert find_call_kwarg_string_values(src, {"Thing"}, "kind") == {"alpha", "beta"}


def test_resolves_local_variable_assigned_from_literal(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def make():\n"
        "    k = 'gamma'\n"
        "    return Thing(kind=k)\n"
    )
    assert find_call_kwarg_string_values(src, {"Thing"}, "kind") == {"gamma"}


def test_resolves_local_variable_assigned_from_ternary_of_literals(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def make(flag):\n"
        "    k = 'x' if flag else 'y'\n"
        "    return Thing(kind=k)\n"
    )
    assert find_call_kwarg_string_values(src, {"Thing"}, "kind") == {"x", "y"}


def test_ignores_non_literal_dynamic_values(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def make(name):\n"
        "    return Thing(kind=name)\n"
    )
    assert find_call_kwarg_string_values(src, {"Thing"}, "kind") == set()


def test_ignores_unrelated_callees(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def make():\n"
        "    return Other(kind='not-counted')\n"
    )
    assert find_call_kwarg_string_values(src, {"Thing"}, "kind") == set()


def test_variable_scoping_does_not_leak_across_functions(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def a():\n"
        "    k = 'from_a'\n"
        "\n"
        "def b():\n"
        "    return Thing(kind=k)\n"  # k is undefined in b's own scope
    )
    assert find_call_kwarg_string_values(src, {"Thing"}, "kind") == set()


def test_real_repo_transformation_construction_sites() -> None:
    """Regression guard against the real bug this module exists to catch:
    a naive literal-only scan would miss cdc_synchronizer/async_fifo,
    which forge/ir/build.py routes through a local ternary variable."""
    found = find_call_kwarg_string_values(
        REPO_ROOT / "forge" / "ir" / "build.py", {"ResolvedTransformation"}, "kind",
    )
    assert found == {
        "pipeline_register", "latency_delay", "slr_crossing", "fanout",
        "cdc_synchronizer", "async_fifo", "tie_off", "gather_scatter",
    }
