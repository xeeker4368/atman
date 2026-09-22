"""**The** list of tools the entity can call. One place, greppable.

Separate from ``registry.py`` because of a dependency direction that has to hold:
a tool module imports :class:`~program.tools.registry.Tool` to declare itself, so
``registry`` cannot import tool modules at its own module scope without a cycle —
and a bottom-of-file import only hides the cycle until something imports the tool
module first, which is exactly how it was found.

So the layering is explicit instead:

* ``registry.py`` — the mechanism. Knows what a tool *is* and how to dispatch
  one. Imports no tool.
* ``catalog.py`` — the contents. Imports every tool and lists them. Imported by
  nothing in the mechanism except ``default_registry()``, at call time.

This keeps task 2.1's actual requirement, which was never about the tuple's
address: *"the full set must be greppable from one place rather than depending
on which modules happened to be imported, since a tool missing because nothing
imported it is a failure with no error message."* That is still true here, and
now the file's name says so.

Adding a tool means editing this file. That is the intended cost of central
registration over import-time decorators.
"""

from __future__ import annotations

from program.tools.creative_write import CREATIVE_WRITE
from program.tools.image_generate import IMAGE_GENERATE
from program.tools.memory_search import MEMORY_SEARCH
from program.tools.registry import Tool
from program.tools.web_fetch import WEB_FETCH
from program.tools.web_search import WEB_SEARCH

#: Every tool, in the order they were built.
#:
#: File ingestion is its own task in Phase 2 and appends itself here when built.
#: Nothing is placed here to have something to register — a placeholder tool
#: would read as built while being nothing.
#:
#: **This is the full set, not the active set.** A tool declaring an ``enabled``
#: predicate is listed here regardless and filtered by ``default_registry()``, so
#: the catalogue stays greppable while config decides what the model is offered.
TOOLS: tuple[Tool, ...] = (
    MEMORY_SEARCH, WEB_SEARCH, WEB_FETCH, IMAGE_GENERATE, CREATIVE_WRITE,
)
