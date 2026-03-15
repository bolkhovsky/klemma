FROM python:3.12-slim

WORKDIR /app

# Install system deps for PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy source first (required by hatchling build)
COPY pyproject.toml ./
COPY src/ ./src/
COPY prompts/ ./prompts/

# Install Python deps (non-editable for production)
RUN pip install --no-cache-dir ".[api,recommended]"

# Create data directory
RUN mkdir -p /data/klemma

ENV KLEMMA_DATA_DIR=/data/klemma
ENV KLEMMA_ENV=production

EXPOSE 8000

CMD ["uvicorn", "klemma.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
