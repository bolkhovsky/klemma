"""Command modules for the Klemma CLI.

Each module registers its commands/groups against ``main`` from ``..cli``.
Importing this package triggers registration of all commands.
"""

from . import acquire, analyze, benchmark, bib, manage, process, research, write  # noqa: F401
