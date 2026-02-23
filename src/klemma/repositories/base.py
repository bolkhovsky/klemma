"""Base class for domain repositories."""


class BaseRepository:
    """Base class sharing a DB connection factory across repositories."""

    def __init__(self, conn_factory):
        self._conn = conn_factory
