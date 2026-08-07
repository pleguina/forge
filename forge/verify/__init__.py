"""forge.verify — permanent compatibility alias for :mod:`forge.verification`.

.. deprecated::
    Import from :mod:`forge.verification` instead. This package is kept
    working indefinitely, not on a deprecation timer: existing plugin
    ``bootstrap.py`` files (and other already-deployed plugin code) import
    ``forge.verify.*`` submodules directly, and FORGE doesn't control when
    those get updated to the new name. See
    docs/development/adr/0005-package-and-cli-naming.md.

Importing this package triggers :mod:`forge.verification`'s own
import-time backend registration exactly once (imported here, not
re-triggered) — accessing verification either as ``forge.verify`` or
``forge.verification`` always shares the same registry state.
"""
import forge.verification as _verification  # noqa: F401 (triggers registration once)
