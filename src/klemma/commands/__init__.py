"""Command modules for the Klemma CLI.

Each module registers its commands/groups against ``main`` from ``..cli``.
Importing this package triggers registration of all commands.
"""

from . import (  # noqa: F401
    acquire,
    analyze,
    benchmark,
    bib,
    decisions,
    manage,
    process,
    research,
    write,
)
