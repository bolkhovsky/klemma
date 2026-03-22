"""Domain repositories — decomposed from StateManager."""

from .base import BaseRepository
from .benchmarks import BenchmarkRepository
from .citations import CitationsRepository
from .decisions import DecisionsRepository
from .embeddings_store import EmbeddingsStoreRepository
from .fragments import FragmentRepository
from .gaps import GapsRepository
from .plans import PlansRepository
from .prune import PruneRepository
from .sources import SourceRepository

__all__ = [
    "BaseRepository",
    "BenchmarkRepository",
    "CitationsRepository",
    "DecisionsRepository",
    "EmbeddingsStoreRepository",
    "FragmentRepository",
    "GapsRepository",
    "PlansRepository",
    "PruneRepository",
    "SourceRepository",
]
