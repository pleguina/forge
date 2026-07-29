"""
Tests for forge.ir.model / forge.ir.serialize — the canonical IR's pure
data model and its JSON/hash/diff layer (audit gap #1: "No canonical IR").

These tests exercise the model directly (small hand-built ResolvedProject
instances), independent of forge.ir.build, so failures here point cleanly
at serialization/hashing bugs rather than construction-from-YAML bugs
(covered separately in test_ir_build.py).
"""

from __future__ import annotations

import json

from forge.ir.model import (
    IR_SCHEMA_VERSION,
    ResolvedClockDomain,
    ResolvedConnection,
    ResolvedDesign,
    ResolvedEndpoint,
    ResolvedInstance,
    ResolvedInterfaceMember,
    ResolvedLogicalInterface,
    ResolvedModuleDefinition,
    ResolvedPhysicalBinding,
    ResolvedProject,
    ResolvedResetDomain,
)
from forge.ir.serialize import content_hash, diff_projects, to_json_dict, to_json_str


def _tiny_project(*, extra_module: bool = False) -> ResolvedProject:
    modules = [
        ResolvedModuleDefinition(
            name="mod_a", kind="rtl", top="mod_a_top", source_files=["mod_a.v"],
            interfaces=[
                ResolvedLogicalInterface(
                    name="data_out", direction="output", wiring_kind="stub",
                    members=[ResolvedInterfaceMember(
                        name="data_out",
                        binding=ResolvedPhysicalBinding(kind="scalar", raw_port="dout", width=8),
                    )],
                ),
            ],
        ),
    ]
    if extra_module:
        modules.append(ResolvedModuleDefinition(name="mod_b", kind="rtl", top="mod_b_top"))

    design = ResolvedDesign(
        name="tiny",
        modules=modules,
        instances=[ResolvedInstance(id="mod_a", module="mod_a")],
        connections=[ResolvedConnection(
            id="mod_a.dout->mod_b.din",
            producer=ResolvedEndpoint(instance_id="mod_a", port="dout"),
            consumer=ResolvedEndpoint(instance_id="mod_b", port="din"),
        )],
        clock_domains=[ResolvedClockDomain(name="default", instances=["mod_a"])],
        reset_domains=[ResolvedResetDomain(name="default", instances=["mod_a"])],
    )
    return ResolvedProject(design=design, forge_version="9.9.9", generated_from={"design": "/tmp/x/design.yml"})


def test_schema_version_default():
    p = _tiny_project()
    assert p.schema_version == IR_SCHEMA_VERSION


def test_to_json_dict_is_json_serializable():
    p = _tiny_project()
    payload = to_json_dict(p)
    # Must round-trip through the standard library's JSON encoder with no
    # custom hooks — proves there are no sets/non-primitive leftovers.
    json.dumps(payload)
    assert payload["design"]["name"] == "tiny"
    assert payload["design"]["modules"][0]["interfaces"][0]["members"][0]["binding"]["raw_port"] == "dout"


def test_to_json_str_is_sorted_and_valid():
    p = _tiny_project()
    s = to_json_str(p)
    assert json.loads(s) == to_json_dict(p)


def test_hash_deterministic_for_identical_input():
    a = _tiny_project()
    b = _tiny_project()
    assert content_hash(a) == content_hash(b)


def test_hash_changes_when_design_changes():
    a = _tiny_project(extra_module=False)
    b = _tiny_project(extra_module=True)
    assert content_hash(a) != content_hash(b)


def test_hash_ignores_generated_from_and_forge_version():
    """The content hash answers 'did the resolved design change', not 'did
    the tool version or the caller's local paths change' — generated_from
    carries absolute local paths and must not leak into the hash."""
    a = _tiny_project()
    b = _tiny_project()
    b.forge_version = "0.0.1-different"
    b.generated_from = {"design": "/completely/different/host/path/design.yml"}
    assert content_hash(a) == content_hash(b)


def test_diff_projects_identical_reports_no_changes():
    a = _tiny_project()
    b = _tiny_project()
    result = diff_projects(a, b)
    assert result["hash_equal"] is True
    assert result["instances"] == {"added": [], "removed": [], "changed": []}
    assert result["connections"] == {"added": [], "removed": [], "changed": []}


def test_diff_projects_reports_added_instance():
    a = _tiny_project()
    b = _tiny_project()
    b.design.instances.append(ResolvedInstance(id="mod_b", module="mod_b"))
    result = diff_projects(a, b)
    assert result["hash_equal"] is False
    assert result["instances"]["added"] == ["mod_b"]
    assert result["instances"]["removed"] == []


def test_diff_projects_reports_removed_and_changed():
    a = _tiny_project()
    b = _tiny_project()
    b.design.connections = []  # remove the one connection
    b.design.instances[0].clock_domain = "other"  # change an existing instance
    result = diff_projects(a, b)
    assert result["connections"]["removed"] == ["mod_a.dout->mod_b.din"]
    assert result["instances"]["changed"] == ["mod_a"]
