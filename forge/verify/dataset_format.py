"""Compatibility alias for :mod:`forge.verification.dataset_format`.

.. deprecated::
    Import from :mod:`forge.verification.dataset_format` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.dataset_format import *  # noqa: F401,F403
