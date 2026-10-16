"""Blocks that are different modules can share one flat instance-id
namespace via ``instance_name`` / ``instance_offset`` (design.yml)."""

from forge.contracts.config import Module
from forge.ir.identifiers import resolved_instance_id


def _mod(name, instances, **kw):
    return Module(name=name, top=name, src=[], instances=instances, **kw)


def test_default_ids_unchanged():
    assert _mod("col", 1).instance_id(0) == "col"
    assert [_mod("dec", 3).instance_id(i) for i in range(3)] == ["dec_0", "dec_1", "dec_2"]


def test_shared_namespace_is_contiguous_across_blocks():
    a = _mod("rb_a", 5, instance_name="rb", instance_offset=0)
    b = _mod("rb_b", 5, instance_name="rb", instance_offset=5)
    ids = [a.instance_id(i) for i in range(5)] + [b.instance_id(i) for i in range(5)]
    assert ids == [f"rb_{i}" for i in range(10)]


def test_single_instance_block_still_suffixed_when_named():
    assert _mod("x", 1, instance_name="rb", instance_offset=7).instance_id(0) == "rb_7"


def test_resolved_instance_id_matches_module_method():
    m = _mod("rb_b", 5, instance_name="rb", instance_offset=5)
    assert resolved_instance_id("rb_b", 2, 5, "rb", 5) == m.instance_id(2) == "rb_7"
