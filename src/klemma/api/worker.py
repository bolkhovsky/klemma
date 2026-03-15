"""RQ worker entry point for async job processing (ADR-009, #186).

Usage:
    rq worker --url redis://localhost:6379

Or via Docker Compose (saas/deploy/docker-compose.yml worker service).
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Start the rq worker."""
    from redis import Redis
    from rq import Worker

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_conn = Redis.from_url(redis_url)

    worker = Worker(["default"], connection=redis_conn)
    worker.work()


if __name__ == "__main__":
    sys.exit(main() or 0)
