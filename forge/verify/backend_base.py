"""Compatibility alias for :mod:`forge.verification.backend_base`.

.. deprecated::
    Import from :mod:`forge.verification.backend_base` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.backend_base import *  # noqa: F401,F403
