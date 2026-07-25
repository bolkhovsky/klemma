#!/usr/bin/env bash
# dev.sh — запуск локальной среды разработки (backend + frontend)
# Использование: ./scripts/dev.sh

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.local"
ENV_EXAMPLE_FILE="$REPO_ROOT/.env.local.example"

# ── Загрузка .env.local ───────────────────────────────────────────────────

if [[ -f "$ENV_FILE" ]]; then
  echo "→ Загружаю $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "⚠  $ENV_FILE не найден — локальные defaults будут использованы."
  if [[ -f "$ENV_EXAMPLE_FILE" ]]; then
    echo "   При необходимости скопируйте шаблон: cp $ENV_EXAMPLE_FILE $ENV_FILE"
  fi
  echo ""
  echo "  # Optional: LLM API key for AI features"
  echo "  ANTHROPIC_API_KEY=sk-ant-..."
  echo ""
  echo "  # Required for local SaaS embeddings"
  echo "  KLEMMA_EMBEDDINGS_BACKEND=litellm"
  echo "  KLEMMA_EMBEDDINGS_MODEL=ollama/bge-m3"
  echo "  KLEMMA_EMBEDDINGS_BASE_URL=http://127.0.0.1:11434"
  echo ""
fi

# Match the Docker Compose defaults so local backend startup works out of the box.
export KLEMMA_EMBEDDINGS_BACKEND="${KLEMMA_EMBEDDINGS_BACKEND:-litellm}"
export KLEMMA_EMBEDDINGS_MODEL="${KLEMMA_EMBEDDINGS_MODEL:-ollama/bge-m3}"
export KLEMMA_EMBEDDINGS_BASE_URL="${KLEMMA_EMBEDDINGS_BASE_URL:-http://127.0.0.1:11434}"

# ── Проверка зависимостей ─────────────────────────────────────────────────

if ! command -v uvicorn &>/dev/null; then
  echo "✗ uvicorn не найден. Установите: pip install 'klemma[api]'" >&2
  exit 1
fi

if ! command -v npm &>/dev/null; then
  echo "✗ npm не найден. Установите Node.js." >&2
  exit 1
fi

# ── Установка npm-зависимостей если нужно ────────────────────────────────

if [[ ! -d "$REPO_ROOT/saas/dashboard/node_modules" ]]; then
  echo "→ npm install..."
  npm --prefix "$REPO_ROOT/saas/dashboard" install --silent
fi

if ! command -v curl &>/dev/null; then
  echo "✗ curl не найден. Он нужен для проверки локального Ollama." >&2
  exit 1
fi

if ! curl -fsS "${KLEMMA_EMBEDDINGS_BASE_URL%/}/api/tags" >/dev/null 2>&1; then
  echo "✗ Ollama недоступен по $KLEMMA_EMBEDDINGS_BASE_URL" >&2
  echo "  Локальный SaaS backend требует embeddings через Ollama." >&2
  echo "  Запустите:" >&2
  echo "    ollama serve" >&2
  echo "    ollama pull bge-m3" >&2
  exit 1
fi

# ── Запуск процессов ──────────────────────────────────────────────────────

cleanup() {
  echo ""
  echo "→ Останавливаю процессы..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "→ Запускаю backend  (http://localhost:8000)"
cd "$REPO_ROOT"
uvicorn klemma.api.app:create_app --factory --port 8000 --reload --log-level warning &
BACKEND_PID=$!

echo "→ Запускаю frontend (http://localhost:5173)"
npm --prefix "$REPO_ROOT/saas/dashboard" run dev &
FRONTEND_PID=$!

echo ""
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo "  Embeddings: $KLEMMA_EMBEDDINGS_MODEL via $KLEMMA_EMBEDDINGS_BASE_URL"
echo "  Остановить: Ctrl+C"
echo ""

# Ждём завершения любого из процессов
wait -n "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || wait
