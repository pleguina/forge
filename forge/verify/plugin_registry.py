"""Compatibility alias for :mod:`forge.verification.plugin_registry`.

.. deprecated::
    Import from :mod:`forge.verification.plugin_registry` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.plugin_registry import *  # noqa: F401,F403
