"""Compatibility alias for :mod:`forge.verification.supported_matrix`.

.. deprecated::
    Import from :mod:`forge.verification.supported_matrix` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.supported_matrix import *  # noqa: F401,F403
