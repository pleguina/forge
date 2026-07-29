"""Tests for forge.docsgen.artifact_walker (release-plan Phase 9, Defect 11)."""
from __future__ import annotations

from forge.docsgen.artifact_walker import ARTIFACT_ROOTS, generate_artifacts_page


def test_deterministic_output() -> None:
    assert generate_artifacts_page() == generate_artifacts_page()


def test_no_absolute_filesystem_paths_leak() -> None:
    page = generate_artifacts_page()
    assert "/home/" not in page
    assert "/data3/" not in page


def test_roots_are_documented_with_working_anchors() -> None:
    page = generate_artifacts_page()
    for root_cls in ARTIFACT_ROOTS.values():
        assert f"### `{root_cls.__name__}`" in page, f"{root_cls.__name__} section missing"


def test_nested_dataclasses_reachable_only_through_quoted_annotations_are_included() -> None:
    """Regression guard: EventResult's fields are all quoted PEP-604-style
    forward references (`"int | None"`, `"list[ArtifactRef]"`, ...) — if
    type resolution silently degraded to plain strings, ArtifactRef/
    CheckResult/VerificationTarget would never be discovered as nested
    models reachable from FlowResult."""
    page = generate_artifacts_page()
    for name in ("ArtifactRef", "CheckResult", "VerificationTarget", "EventResult"):
        assert f"### `{name}`" in page, f"{name} section missing (nested-discovery regression)"


def test_disclaims_being_json_schema() -> None:
    """This page must never be mistaken for actual JSON Schema (none
    exists in the repo today) — it explicitly disclaims that."""
    page = generate_artifacts_page()
    assert "not** JSON Schema" in page.replace("\n", " ")
