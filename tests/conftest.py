"""Shared pytest fixtures and configuration.

Sets KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1 for all tests so the embeddings
security guard (introduced in the upload-pipeline-speedup PR) does not
block test app startup. All API tests use mocked/disabled embeddings
anyway — this flag simply allows the empty/test config to pass through.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def allow_remote_embeddings_in_tests():
    """Bypass the local-only embeddings enforcement for the test suite."""
    prev = os.environ.get("KLEMMA_EMBEDDINGS_ALLOW_REMOTE")
    os.environ["KLEMMA_EMBEDDINGS_ALLOW_REMOTE"] = "1"
    yield
    if prev is None:
        os.environ.pop("KLEMMA_EMBEDDINGS_ALLOW_REMOTE", None)
    else:
        os.environ["KLEMMA_EMBEDDINGS_ALLOW_REMOTE"] = prev
