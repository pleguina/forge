"""Tests for forge.docsgen._type_resolution — the PEP 604 / postponed-
evaluation workaround this package's field-type resolution depends on.
Exercises the exact real classes that exposed the
bug during development: quoted ``"X | None"`` forward-references under
``from __future__ import annotations`` evaluate, on Python < 3.10, to a
``ForwardRef`` (a useless opaque string) unless the outer quote layer is
stripped and the union is rewritten before eval.
"""
from __future__ import annotations

import typing
from dataclasses import dataclass

from forge.docsgen._type_resolution import (
    _rewrite_pep604_union,
    _strip_redundant_quotes,
    resolve_dataclass_field_types,
)
from forge.verification.results import ArtifactRef, EventResult
from forge.verification.dataset_format import DatasetMetadata


def test_strip_redundant_quotes_single_layer() -> None:
    assert _strip_redundant_quotes("'int | None'") == "int | None"
    assert _strip_redundant_quotes('"int | None"') == "int | None"


def test_strip_redundant_quotes_noop_when_unquoted() -> None:
    assert _strip_redundant_quotes("int | None") == "int | None"


def test_rewrite_pep604_union_top_level() -> None:
    assert _rewrite_pep604_union("int | None") == "typing.Union[int, None]"
    assert _rewrite_pep604_union("str") == "str"


def test_rewrite_pep604_union_respects_bracket_nesting() -> None:
    # The '|' inside dict[str, str] must not be split on (there isn't one
    # here, but the comma inside brackets must not confuse a naive split);
    # the real top-level '|' before None is what gets rewritten.
    assert _rewrite_pep604_union("dict[str, str] | None") == "typing.Union[dict[str, str], None]"


def test_resolves_quoted_pep604_union_to_a_real_optional_type() -> None:
    hints = resolve_dataclass_field_types(EventResult)
    event_index_type = hints["event_index"]
    assert typing.get_origin(event_index_type) is typing.Union
    assert set(typing.get_args(event_index_type)) == {int, type(None)}
    # Regression guard for the exact bug found during development: this
    # must not be a bare, un-resolved ForwardRef/string.
    assert not isinstance(event_index_type, str)
    assert not isinstance(event_index_type, typing.ForwardRef)


def test_resolves_quoted_generic_container_to_a_real_generic_alias() -> None:
    hints = resolve_dataclass_field_types(EventResult)
    artifacts_type = hints["artifacts"]
    assert typing.get_origin(artifacts_type) is list
    assert typing.get_args(artifacts_type)[0].__name__ == "ArtifactRef"


def test_resolves_cross_module_forward_reference() -> None:
    hints = resolve_dataclass_field_types(ArtifactRef)
    stage_type = hints["stage"]
    assert typing.get_origin(stage_type) is typing.Union
    non_none = [a for a in typing.get_args(stage_type) if a is not type(None)]
    assert len(non_none) == 1
    assert non_none[0].__name__ == "ExecutionStage"


def test_resolves_quoted_dict_with_optional() -> None:
    hints = resolve_dataclass_field_types(DatasetMetadata)
    units_type = hints["units"]
    assert typing.get_origin(units_type) is typing.Union
    non_none = [a for a in typing.get_args(units_type) if a is not type(None)]
    assert typing.get_origin(non_none[0]) is dict


def test_non_postponed_dataclass_field_types_pass_through_unchanged() -> None:
    @dataclass
    class Plain:
        x: int
        y: "str"

    hints = resolve_dataclass_field_types(Plain)
    assert hints["x"] is int
    assert hints["y"] is str
