"""Compatibility alias for :mod:`forge.analysis.dashboards.attachments`.

.. deprecated::
    Import from :mod:`forge.analysis.dashboards.attachments` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin tool code may import ``forge.analyze.*`` directly and FORGE
    doesn't control when those get updated.
"""
from forge.analysis.dashboards.attachments import *  # noqa: F401,F403
