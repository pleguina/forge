"""forge.analyze — permanent compatibility alias for :mod:`forge.analysis`.

.. deprecated::
    Import from :mod:`forge.analysis` instead. This package is kept
    working indefinitely, not on a deprecation timer: existing plugin
    tool code (e.g. ``forge.analyze.dashboards.attachments``,
    ``forge.analyze.throughput_static.model``) imports ``forge.analyze.*``
    submodules directly, and FORGE doesn't control when those get
    updated to the new name. See
    docs/development/adr/0005-package-and-cli-naming.md.
"""
import forge.analysis as _analysis  # noqa: F401
