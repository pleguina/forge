"""Compatibility alias for :mod:`forge.verification.layout`.

.. deprecated::
    Import from :mod:`forge.verification.layout` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.layout import *  # noqa: F401,F403
