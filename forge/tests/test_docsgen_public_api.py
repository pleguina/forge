"""Tests for forge.docsgen.public_api.

Per the release plan's public-API freeze policy: only `__all__`-listed
symbols are stable, private (`_`-prefixed, non-dunder) names in `__all__`
are a reportable inconsistency rather than promoted API, and every stable
symbol must actually be importable — this file is the "imports every
stable symbol" proof the policy calls for.
"""
from __future__ import annotations

import importlib

from forge.docsgen.public_api import build_inventory, discover_public_api, generate_public_api_page


def test_deterministic_output() -> None:
    assert generate_public_api_page() == generate_public_api_page()


def test_every_public_symbol_is_actually_importable() -> None:
    public, _excluded = discover_public_api()
    assert public, "expected at least one public symbol across forge/*'s __all__"
    for symbol in public:
        module = importlib.import_module(symbol.module)
        assert hasattr(module, symbol.name), (
            f"{symbol.module}.{symbol.name} is listed in __all__ but not importable"
        )


def test_underscore_prefixed_all_entries_are_excluded_not_promoted() -> None:
    public, excluded = discover_public_api()
    for symbol in public:
        assert not (symbol.name.startswith("_") and not symbol.name.startswith("__")), (
            f"{symbol.module}.{symbol.name} is private-by-convention and must not "
            "appear in the stable public API"
        )
    # forge.core.utils.__all__ is the known, currently-existing case of this.
    assert any(s.module == "forge.core.utils" for s in excluded)


def test_dunder_version_is_not_treated_as_private() -> None:
    public, excluded = discover_public_api()
    assert any(s.name == "__version__" for s in public)
    assert not any(s.name == "__version__" for s in excluded)


def test_inventory_matches_page_contents() -> None:
    inventory = build_inventory()
    page = generate_public_api_page()
    for entry in inventory["public"]:
        assert f"`{entry['name']}`" in page
