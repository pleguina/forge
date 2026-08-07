"""Compatibility alias for :mod:`forge.verification.readmemh_stimulus`.

.. deprecated::
    Import from :mod:`forge.verification.readmemh_stimulus` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.readmemh_stimulus import *  # noqa: F401,F403
