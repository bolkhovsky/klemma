# Bonum meeting-analytics portal image.
# Same klemma package as the SaaS image, but ALSO bakes the built dashboard so
# the container is self-contained (serves SPA + API at root via KLEMMA_SERVE_SPA).
# Build context = repo root. The dashboard must be built first
# (npm run build with VITE_API_BASE='' VITE_PORTAL_ONLY=1 → saas/dashboard/dist).
FROM python:3.12-slim AS deps

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/klemma/__init__.py ./src/klemma/
RUN pip install --no-cache-dir ".[api,recommended]"

FROM deps AS app

COPY src/ ./src/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/
# Pre-built SPA (portal-only build) baked in and served by FastAPI.
COPY saas/dashboard/dist/ ./dashboard/

RUN mkdir -p /data/saas /data/meetings

ENV PYTHONPATH=/app/src
ENV KLEMMA_ENV=production
ENV KLEMMA_PROMPTS_DIR=/app/prompts
ENV KLEMMA_SERVE_SPA=/app/dashboard
# Serve the marketing landing at "/" (baked into the SPA build via public/);
# the portal SPA stays reachable on every other route. Unset elsewhere.
ENV KLEMMA_SPA_ROOT_FILE=portal-soveshchaniy.html
ENV KLEMMA_DATA_DIR=/data/saas
ENV KLEMMA_BONUM_PROJECT_ROOT=/data/meetings

EXPOSE 8000

# Single worker — low-traffic single-client portal; keeps RAM footprint small.
CMD ["uvicorn", "klemma.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
