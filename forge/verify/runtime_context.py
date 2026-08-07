"""Compatibility alias for :mod:`forge.verification.runtime_context`.

.. deprecated::
    Import from :mod:`forge.verification.runtime_context` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.runtime_context import *  # noqa: F401,F403
