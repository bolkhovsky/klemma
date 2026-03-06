"""Embedding storage repository — vector CRUD and coverage stats."""

import struct
from typing import Optional

from .base import BaseRepository


class EmbeddingsStoreRepository(BaseRepository):

    def save_embedding(
        self, source_id: str, embedding: list[float], model: str
    ):
        """Store embedding vector as BLOB with model name."""
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET embedding=?, embedding_model=? WHERE id=?",
                (blob, model, source_id),
            )

    def get_embedding(self, source_id: str) -> Optional[tuple[list[float], str]]:
        """Retrieve embedding vector and model name for a source.

        Returns (vector, model_name) or None if no embedding stored.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT embedding, embedding_model FROM sources WHERE id=?",
                (source_id,),
            ).fetchone()
            if not row or not row["embedding"]:
                return None
            blob = row["embedding"]
            n = len(blob) // 4  # float32 = 4 bytes
            vec = list(struct.unpack(f"{n}f", blob))
            return (vec, row["embedding_model"] or "")

    def get_all_embeddings(
        self, model: Optional[str] = None
    ) -> dict[str, list[float]]:
        """Get all source embeddings, optionally filtered by model.

        Returns {source_id: vector}.
        """
        with self._conn() as conn:
            if model:
                cur = conn.execute(
                    "SELECT id, embedding FROM sources "
                    "WHERE embedding IS NOT NULL AND embedding_model=?",
                    (model,),
                )
            else:
                cur = conn.execute(
                    "SELECT id, embedding FROM sources WHERE embedding IS NOT NULL"
                )
            result = {}
            for row in cur.fetchall():
                blob = row["embedding"]
                n = len(blob) // 4
                result[row["id"]] = list(struct.unpack(f"{n}f", blob))
            return result

    def get_embedding_stats(self) -> dict:
        """Get embedding coverage stats."""
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM sources WHERE status='completed'"
            ).fetchone()["cnt"]
            embedded = conn.execute(
                "SELECT COUNT(*) as cnt FROM sources WHERE status='completed' AND embedding IS NOT NULL"
            ).fetchone()["cnt"]
            models = {}
            cur = conn.execute(
                "SELECT embedding_model, COUNT(*) as cnt FROM sources "
                "WHERE embedding IS NOT NULL GROUP BY embedding_model"
            )
            for row in cur.fetchall():
                models[row["embedding_model"] or "unknown"] = row["cnt"]
            return {"total": total, "embedded": embedded, "models": models}
