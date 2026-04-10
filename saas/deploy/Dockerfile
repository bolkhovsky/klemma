FROM python:3.12-slim AS deps

WORKDIR /app

# Install system deps (PyMuPDF + git for sync)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (cached unless pyproject.toml changes)
COPY pyproject.toml README.md ./
COPY src/klemma/__init__.py ./src/klemma/
RUN pip install --no-cache-dir ".[api,recommended]"

FROM deps AS app

# Copy full source. We skip `pip install .` here because pyproject.toml
# excludes src/klemma/api from the wheel (CLI-only package for researchers).
# Instead we run from source via PYTHONPATH so the API module is visible.
COPY src/ ./src/
COPY prompts/ ./prompts/

# Create data directory
RUN mkdir -p /data/klemma

ENV KLEMMA_DATA_DIR=/data/klemma
ENV KLEMMA_ENV=production
ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "klemma.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
