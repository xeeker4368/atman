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

from program.tools.memory_search import MEMORY_SEARCH
from program.tools.registry import Tool

#: Every tool, in the order they were built.
#:
#: ``web_search``, ``web_fetch`` and file ingestion are each their own task in
#: Phase 2 and append themselves here when built. Nothing is placed here to have
#: something to register — a placeholder tool would read as built while being
#: nothing.
TOOLS: tuple[Tool, ...] = (MEMORY_SEARCH,)
