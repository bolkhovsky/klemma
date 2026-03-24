#!/usr/bin/env bash
# Run backend (FastAPI) and frontend (Vite) together for local development.
# Usage: ./scripts/dev.sh
# Stop: Ctrl+C (kills both processes)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cleanup() {
    echo ""
    echo "Stopping..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
    wait $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# Backend: FastAPI on port 8000
echo "Starting backend on http://localhost:8000 ..."
cd "$PROJECT_ROOT"
uvicorn klemma.api.app:create_app --factory --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Wait for backend to be ready
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "Backend ready."
        break
    fi
    sleep 0.5
done

# Frontend: Vite on port 5173 (proxies /api/* → localhost:8000)
echo "Starting frontend on http://localhost:5173 ..."
cd "$PROJECT_ROOT/saas/dashboard"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "=== Dev servers running ==="
echo "  Frontend: http://localhost:5173"
echo "  Backend:  http://localhost:8000"
echo "  API docs: http://localhost:8000/docs"
echo "==========================="
echo "Press Ctrl+C to stop both."
echo ""

wait
