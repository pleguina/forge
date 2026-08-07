"""Generated-documentation-reference system.

Generates ``docs/reference/*.md`` pages from live FORGE objects — CLI
reference, artifact-model reference, diagnostic catalogue, canonical
role/protocol/interface-member/transformation vocabularies, and the
support matrix — instead of hand-duplicating them, so these pages can
never silently drift from the code they describe.

Every ``generate_*_page()`` function is pure: given the current in-process
state of the modules it reads, it returns a markdown string with no
timestamps, no absolute filesystem paths, and deterministic ordering. Run
``python -m forge.docsgen`` to regenerate every page under ``docs/reference/``,
or ``python -m forge.docsgen --check`` to verify the on-disk pages are
still up to date without writing (a staleness check).
"""
