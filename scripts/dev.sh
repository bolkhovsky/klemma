#!/usr/bin/env bash
# dev.sh — запуск локальной среды разработки (backend + frontend)
# Использование: ./scripts/dev.sh

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.local"

# ── Загрузка .env.local ───────────────────────────────────────────────────

if [[ -f "$ENV_FILE" ]]; then
  echo "→ Загружаю $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "⚠  $ENV_FILE не найден — создайте его из примера ниже:"
  echo ""
  echo "  KLEMMA_JWT_SECRET=\$(python3 -c \"import secrets; print(secrets.token_hex(32))\")"
  echo "  ANTHROPIC_API_KEY=sk-ant-..."
  echo ""
fi

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
echo "  Остановить: Ctrl+C"
echo ""

# Ждём завершения любого из процессов
wait -n "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || wait
