"""Tests for embeddings security validation (Part 2 of upload-pipeline-speedup).

Verifies that _validate_embeddings_config() blocks non-local backends
and that KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1 properly bypasses the guard.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestValidateEmbeddingsConfig:
    def _call(self, env: dict):
        from klemma.api.tasks import _validate_embeddings_config

        with patch.dict(os.environ, env, clear=False):
            _validate_embeddings_config()

    def test_requires_litellm_backend(self):
        with pytest.raises(RuntimeError, match="litellm"):
            self._call({
                "KLEMMA_EMBEDDINGS_BACKEND": "openai",
                "KLEMMA_EMBEDDINGS_MODEL": "ollama/bge-m3",
                "KLEMMA_EMBEDDINGS_BASE_URL": "http://ollama:11434",
                "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "",
            })

    def test_requires_ollama_model_prefix(self):
        with pytest.raises(RuntimeError, match="ollama/"):
            self._call({
                "KLEMMA_EMBEDDINGS_BACKEND": "litellm",
                "KLEMMA_EMBEDDINGS_MODEL": "voyage/v3",
                "KLEMMA_EMBEDDINGS_BASE_URL": "http://ollama:11434",
                "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "",
            })

    def test_requires_base_url(self):
        with pytest.raises(RuntimeError, match="BASE_URL"):
            self._call({
                "KLEMMA_EMBEDDINGS_BACKEND": "litellm",
                "KLEMMA_EMBEDDINGS_MODEL": "ollama/bge-m3",
                "KLEMMA_EMBEDDINGS_BASE_URL": "",
                "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "",
            })

    def test_requires_backend_set(self):
        with pytest.raises(RuntimeError, match="BACKEND is not set"):
            self._call({
                "KLEMMA_EMBEDDINGS_BACKEND": "",
                "KLEMMA_EMBEDDINGS_MODEL": "ollama/bge-m3",
                "KLEMMA_EMBEDDINGS_BASE_URL": "http://ollama:11434",
                "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "",
            })

    def test_allow_remote_bypasses_check(self):
        # Should not raise even with invalid backend
        self._call({
            "KLEMMA_EMBEDDINGS_BACKEND": "openai",
            "KLEMMA_EMBEDDINGS_MODEL": "text-embedding-3-small",
            "KLEMMA_EMBEDDINGS_BASE_URL": "",
            "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "1",
        })

    def test_valid_local_config_passes(self):
        self._call({
            "KLEMMA_EMBEDDINGS_BACKEND": "litellm",
            "KLEMMA_EMBEDDINGS_MODEL": "ollama/bge-m3",
            "KLEMMA_EMBEDDINGS_BASE_URL": "http://ollama:11434",
            "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "",
        })

    def test_all_errors_reported_together(self):
        with pytest.raises(RuntimeError) as exc_info:
            self._call({
                "KLEMMA_EMBEDDINGS_BACKEND": "openai",
                "KLEMMA_EMBEDDINGS_MODEL": "voyage/v3",
                "KLEMMA_EMBEDDINGS_BASE_URL": "",
                "KLEMMA_EMBEDDINGS_ALLOW_REMOTE": "",
            })
        msg = str(exc_info.value)
        # All three errors should appear in one RuntimeError
        assert "litellm" in msg
        assert "ollama/" in msg
        assert "BASE_URL" in msg
