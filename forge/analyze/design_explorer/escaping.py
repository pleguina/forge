"""Centralized escaping helpers for every renderer in this package
(release-plan Phase 8, slices 8.1/8.2).

Every string that reaches a rendered artifact — module/instance/interface
names, diagnostic messages, source paths, parameter values, matching-
evidence text — originates in project-authored YAML/RTL, not framework-
controlled text. Strict escaping is therefore a first-class concern here,
not an afterthought: every renderer in this package routes text through
one of these functions rather than interpolating raw strings.
"""
from __future__ import annotations

import hashlib
import html as _html


def dot_id(raw: str) -> str:
    """A Graphviz DOT quoted identifier/string for *raw* — always quoted
    (never the bare-alnum form), since real IR ids routinely contain
    characters (``.``, ``->``, ``:``, ``$``) that are not legal in an
    unquoted DOT identifier."""
    escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dot_label(text: str) -> str:
    """A Graphviz DOT quoted plain-text label for *text*. Real embedded
    newlines are collapsed to spaces — a project-authored string
    (diagnostic message, parameter value) should never be able to inject
    an unexpected line break into a node's rendered shape."""
    single_line = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    escaped = single_line.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dot_html_label_text(text: str) -> str:
    """HTML-entity-escaped text for use inside a Graphviz HTML-like label
    (``label=<...>``) — e.g. an interface sub-row. Escapes the same four
    characters HTML text content requires (``&``, ``<``, ``>``, ``"``)."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def html_text(text: str) -> str:
    """HTML-escaped text content (no attribute-quote escaping needed)."""
    return _html.escape(text, quote=False)


def html_attr(text: str) -> str:
    """HTML-escaped text for use inside a double-quoted attribute value."""
    return _html.escape(text, quote=True)


def json_script_safe(json_text: str) -> str:
    """Neutralize a literal ``</script`` substring inside *json_text* so it
    can be safely embedded in a ``<script type="application/json">...
    </script>`` data island.

    A JSON string value can legally contain the four characters ``<``,
    ``/``, ``s``, ``c``... — a project-authored module name or diagnostic
    message containing the literal text ``</script>`` would otherwise
    terminate the surrounding ``<script>`` element early, regardless of
    the JSON itself being syntactically valid. ``\\/`` is a legal
    (if not required) JSON escape for ``/`` per the JSON spec, so replacing
    every ``</`` with ``<\\/`` keeps the payload valid JSON while breaking
    the literal ``</script`` sequence an HTML parser scans for.
    """
    return json_text.replace("</", "<\\/")


def dom_safe_id(object_id: str) -> str:
    """A stable, derived id safe for use as a CSS selector or DOM element
    id — real IR ids (e.g. ``"dec_0.out->trig.in"``) are never used
    directly for this, since they contain characters that are not legal
    (or are awkward to escape) in a CSS selector. The real ``object_id``
    is still carried separately (e.g. in a ``data-object-id`` attribute)
    for lookups and display."""
    digest = hashlib.sha256(object_id.encode("utf-8")).hexdigest()[:16]
    return f"n-{digest}"
