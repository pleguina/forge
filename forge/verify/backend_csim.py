"""Compatibility alias for :mod:`forge.verification.backend_csim`.

.. deprecated::
    Import from :mod:`forge.verification.backend_csim` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.backend_csim import *  # noqa: F401,F403
