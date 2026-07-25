"""Tests for _enqueue_auto_sentences post-hook (auto-generation on upload)."""

from __future__ import annotations

from unittest.mock import patch

from klemma.api.tasks import _enqueue_auto_sentences


def test_enqueue_falls_back_to_sync_without_redis():
    """When Redis is unreachable the task runs synchronously so the user
    still gets sentences generated — just slower — instead of silently
    dropping the job.
    """
    calls: list[tuple] = []

    def fake_generate_sentences_task(*args, **kwargs):
        calls.append(args)
        return {"status": "completed"}

    with patch(
        "klemma.api.tasks.generate_sentences_task",
        fake_generate_sentences_task,
    ):
        # Force the rq import path to fail so the sync fallback fires.
        with patch("redis.Redis.from_url", side_effect=RuntimeError("no redis")):
            job_id = _enqueue_auto_sentences(
                project_id="proj-x",
                citekey="smith2023",
                user_id="user-a",
                data_dir="/tmp/kl-nope",
            )

    assert job_id is None  # sync path returns None, not a job id
    assert len(calls) == 1
    args = calls[0]
    # signature: (project_id, citekey, data_dir, user_id, mode)
    assert args[0] == "proj-x"
    assert args[1] == "smith2023"
    assert args[3] == "user-a"
    assert args[4] == "missing"  # mode="missing" so re-runs are no-ops


def test_enqueue_sync_fallback_swallows_errors():
    """Post-hook must never crash process_source. If the sync fallback
    itself raises, we log and return None — nothing re-raises.
    """
    def raising_task(*args, **kwargs):
        raise RuntimeError("simulated AI failure")

    with patch("klemma.api.tasks.generate_sentences_task", raising_task):
        with patch("redis.Redis.from_url", side_effect=RuntimeError("no redis")):
            # Must not raise.
            job_id = _enqueue_auto_sentences(
                project_id="p",
                citekey="ck",
                user_id="u",
                data_dir="/tmp/x",
            )

    assert job_id is None
