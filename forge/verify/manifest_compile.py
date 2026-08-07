"""Compatibility alias for :mod:`forge.verification.manifest_compile`.

.. deprecated::
    Import from :mod:`forge.verification.manifest_compile` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.manifest_compile import *  # noqa: F401,F403
