"""forge.topgen.generators — deprecated alias for
:mod:`forge.generation.generators`.

.. deprecated::
    Import from :mod:`forge.generation.generators` instead. Unlike
    `forge.verify`/`forge.analyze`, this alias is on a deprecation timer,
    not kept permanently: `forge.topgen.generators` is part of the frozen
    public API (docs/reference/public-python-api.md), so it must keep
    working for at least one minor release, but is not promised forever.
    See docs/development/adr/0005-package-and-cli-naming.md.
"""
from warnings import warn as _warn

_warn(
    "forge.topgen.generators is deprecated; import from "
    "forge.generation.generators instead.",
    DeprecationWarning,
    stacklevel=2,
)

from forge.generation.generators import *  # noqa: F401,F403
from forge.generation.generators import __all__  # noqa: F401
