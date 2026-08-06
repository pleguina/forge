"""Tests for forge.docsgen.type_formatter."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from forge.docsgen.type_formatter import format_type


def test_primitives() -> None:
    assert format_type(str) == "str"
    assert format_type(int) == "int"
    assert format_type(bool) == "bool"
    assert format_type(type(None)) == "None"
    assert format_type(Any) == "Any"


def test_optional() -> None:
    assert format_type(Optional[str]) == "Optional[str]"
    assert format_type(Optional[int]) == "Optional[int]"


def test_generic_containers_normalize_to_lowercase_builtin_spelling() -> None:
    assert format_type(List[int]) == "list[int]"
    assert format_type(list[int]) == "list[int]"
    assert format_type(Dict[str, Any]) == "dict[str, Any]"
    assert format_type(dict[str, Any]) == "dict[str, Any]"


def test_bare_generic_without_args() -> None:
    assert format_type(list) == "list"
    assert format_type(dict) == "dict"


def test_union_non_optional() -> None:
    assert format_type(Union[int, str]) == "Union[int, str]"


def test_optional_union_of_multiple() -> None:
    assert format_type(Optional[Union[int, str]]) == "Optional[Union[int, str]]"


def test_variadic_tuple() -> None:
    assert format_type(Tuple[int, ...]) == "tuple[int, ...]"
    assert format_type(tuple[str, ...]) == "tuple[str, ...]"


def test_user_defined_class() -> None:
    class Foo:
        pass

    assert format_type(Foo) == "Foo"
    assert format_type(Optional[Foo]) == "Optional[Foo]"
    assert format_type(List[Foo]) == "list[Foo]"


def test_deterministic_across_repeated_calls() -> None:
    tp = Optional[Dict[str, List[int]]]
    assert format_type(tp) == format_type(tp) == "Optional[dict[str, list[int]]]"
