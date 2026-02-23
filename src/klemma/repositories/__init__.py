"""Domain repositories — decomposed from StateManager."""

from .base import BaseRepository
from .citations import CitationsRepository
from .embeddings_store import EmbeddingsStoreRepository
from .fragments import FragmentRepository
from .gaps import GapsRepository
from .plans import PlansRepository
from .prune import PruneRepository
from .sources import SourceRepository

__all__ = [
    "BaseRepository",
    "CitationsRepository",
    "EmbeddingsStoreRepository",
    "FragmentRepository",
    "GapsRepository",
    "PlansRepository",
    "PruneRepository",
    "SourceRepository",
]
