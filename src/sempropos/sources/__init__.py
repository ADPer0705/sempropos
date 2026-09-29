"""Documentation sources for sempropos.

Currently implemented:

* :mod:`sempropos.sources.man` — system man pages (sections 1 and 8).

Planned (same :class:`~sempropos.sources.base.Source` interface):

* ``tldr`` — community tldr page examples.
* ``help`` — parsed ``<tool> --help`` output.
"""

from sempropos.sources.base import ParsedTool, Source, ToolRecord
from sempropos.sources.man import ManSource

__all__ = ["ParsedTool", "Source", "ToolRecord", "ManSource"]
