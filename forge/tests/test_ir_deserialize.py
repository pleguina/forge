"""
Tests for forge.ir.deserialize — IR schema compatibility, migration, and
the model-driven round-trip that replaced the hand-written reader.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.ir.build import build_project_ir
from forge.ir.deserialize import (
    IrSchemaError,
    check_ir_schema_version,
    from_json_dict,
    migrate_ir_payload,
    parse_ir_version,
)
from forge.ir.model import IR_SCHEMA_VERSION
from forge.ir.serialize import content_hash, to_json_dict

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"


@pytest.fixture(scope="module")
def project():
    return build_project_ir(PASSTHROUGH_DESIGN, contracts_from=PASSTHROUGH_MODULES)


def test_round_trip_is_exact(project):
    """The whole point: writing and reading back a real design must produce
    the same design, field for field — not "close enough to diff"."""
    payload = json.loads(json.dumps(to_json_dict(project)))
    back, notes = from_json_dict(payload)

    assert notes == []
    assert to_json_dict(back) == to_json_dict(project)
    assert content_hash(back) == content_hash(project)


def test_round_trip_survives_a_newly_added_field(project):
    """The regression this module exists for: a field added to the model
    but not to the reader used to vanish on the round-trip, and the design
    silently 'changed'. Nothing here enumerates fields, so a new one is
    carried automatically — asserted on the two most recently added."""
    payload = json.loads(json.dumps(to_json_dict(project)))
    back, _ = from_json_dict(payload)

    assert [m.rtl_sources for m in back.design.modules] == [
        m.rtl_sources for m in project.design.modules
    ]
    assert [m.declaration_order for m in back.design.modules] == [
        m.declaration_order for m in project.design.modules
    ]


def test_unknown_fields_from_a_newer_minor_are_ignored(project):
    payload = json.loads(json.dumps(to_json_dict(project)))
    major, minor, patch = parse_ir_version(IR_SCHEMA_VERSION)
    payload["schema_version"] = f"{major}.{minor + 1}.0"
    payload["design"]["something_added_later"] = {"whatever": True}
    payload["design"]["modules"][0]["also_added_later"] = 3

    back, notes = from_json_dict(payload)

    assert any("newer than this build" in n for n in notes)
    assert back.design.modules[0].name == project.design.modules[0].name


def test_a_different_major_is_refused(project):
    payload = json.loads(json.dumps(to_json_dict(project)))
    payload["schema_version"] = "1.0.0"

    with pytest.raises(IrSchemaError) as exc:
        from_json_dict(payload)

    assert "incompatible" in str(exc.value)


@pytest.mark.parametrize("bad", ["0.2", "x.y.z", "0.2.0.1", "-1.0.0", 3])
def test_a_malformed_version_is_refused(bad):
    with pytest.raises(IrSchemaError):
        parse_ir_version(bad)


def test_current_version_needs_no_warning():
    assert check_ir_schema_version(IR_SCHEMA_VERSION) is None


def test_migration_renames_the_0_1_0_transformation_kinds():
    """A 0.1.0 IR called a pipeline register 'register' and a latency delay
    'delay'. Read as-is they'd be kinds nothing recognises — a design whose
    build manifest must compile RegisterStage.v would silently not."""
    payload = {
        "schema_version": "0.1.0",
        "design": {
            "name": "d",
            "connections": [
                {
                    "id": "c1",
                    "producer": {"instance_id": "a", "port": "o"},
                    "consumer": {"instance_id": "b", "port": "i"},
                    "transformations": [
                        {"id": "x1", "kind": "register", "cycles": 2},
                        {"id": "x2", "kind": "delay", "cycles": 3},
                    ],
                }
            ],
        },
    }

    project, notes = from_json_dict(payload)

    kinds = [x.kind for x in project.design.connections[0].transformations]
    assert kinds == ["pipeline_register", "latency_delay"]
    assert any("0.1.0 → 0.2.0" in n for n in notes)
    assert project.schema_version == IR_SCHEMA_VERSION


def test_migration_is_idempotent_on_a_current_payload(project):
    payload = json.loads(json.dumps(to_json_dict(project)))
    once, notes_once = migrate_ir_payload(payload)
    twice, notes_twice = migrate_ir_payload(once)

    assert notes_once == [] and notes_twice == []
    assert once == twice


def test_a_payload_that_is_not_an_ir_document_is_refused():
    with pytest.raises(IrSchemaError):
        from_json_dict({"hello": "world"})


def test_diff_reports_both_sides_schema_versions(project):
    from forge.ir.serialize import diff_projects

    result = diff_projects(project, project)

    assert result["schema_a"] == result["schema_b"] == IR_SCHEMA_VERSION
    assert result["hash_equal"] is True
