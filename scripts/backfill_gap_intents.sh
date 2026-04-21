#!/usr/bin/env bash
# Backfill citation intents for a user's citation graph entries.
#
# Calls POST /admin/backfill/citation-intents in a cursor loop until all
# papers are processed. Requires admin credentials.
#
# Usage:
#   ./scripts/backfill_gap_intents.sh [--dry-run] <target_user_id> [batch_size]
#
# Options:
#   --dry-run   Run only the first batch with &dry_run=true on the endpoint.
#               The AI extraction runs but DB writes are skipped — truly non-mutating.
#               Use batch_size=5 with --dry-run to see a small sample.
#
# Environment:
#   KLEMMA_API_URL        — API base URL (default: https://litresearch.ru/api)
#   KLEMMA_ADMIN_EMAIL    — Admin account email (required)
#   KLEMMA_ADMIN_PASSWORD — Admin account password (required)
#
# Examples:
#   # Full backfill
#   KLEMMA_ADMIN_EMAIL=admin@example.com \
#   KLEMMA_ADMIN_PASSWORD=secret \
#   ./scripts/backfill_gap_intents.sh user-uuid-here 20
#
#   # Dry-run: show first 3 papers without committing anything beyond that batch
#   KLEMMA_ADMIN_EMAIL=admin@example.com \
#   KLEMMA_ADMIN_PASSWORD=secret \
#   ./scripts/backfill_gap_intents.sh --dry-run user-uuid-here 3

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
  shift
fi

API="${KLEMMA_API_URL:-https://litresearch.ru/api}"
ADMIN_EMAIL="${KLEMMA_ADMIN_EMAIL:?Set KLEMMA_ADMIN_EMAIL}"
ADMIN_PASS="${KLEMMA_ADMIN_PASSWORD:?Set KLEMMA_ADMIN_PASSWORD}"
TARGET_USER_ID="${1:?Usage: backfill_gap_intents.sh [--dry-run] <target_user_id> [batch_size]}"
BATCH_SIZE="${2:-20}"

# ---------------------------------------------------------------------------
# Auth helpers — access tokens expire after KLEMMA_ACCESS_TOKEN_EXPIRE_MINUTES
# (default 15 min).  A large backfill spans many batches and will outlive a
# single token.  We refresh lazily: _maybe_refresh_token() only calls the
# refresh endpoint when the token is within TOKEN_REFRESH_BUFFER_SECS of
# expiry — not on every batch — to avoid hitting the 10/min rate limit and
# invalidating other sessions via token rotation.
# ---------------------------------------------------------------------------

ADMIN_TOKEN=""
REFRESH_TOKEN=""
TOKEN_ISSUED_AT=0
# Refresh 3 minutes before the default 15-minute expiry.
TOKEN_TTL_SECS="${KLEMMA_TOKEN_TTL_SECS:-720}"

_login() {
  local resp
  resp=$(curl -sf -X POST "$API/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$ADMIN_EMAIL\", \"password\": \"$ADMIN_PASS\"}") || {
      echo "ERROR: Login failed"
      exit 1
    }
  ADMIN_TOKEN=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
  REFRESH_TOKEN=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['refresh_token'])")
  TOKEN_ISSUED_AT=$SECONDS
}

# Rotate both tokens via the refresh endpoint; falls back to full re-login if
# the refresh token itself has expired (30-day default).
_do_refresh() {
  local resp
  if resp=$(curl -sf -X POST "$API/auth/refresh" \
    -H "Content-Type: application/json" \
    -d "{\"refresh_token\": \"$REFRESH_TOKEN\"}" 2>/dev/null); then
    ADMIN_TOKEN=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
    REFRESH_TOKEN=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['refresh_token'])")
    TOKEN_ISSUED_AT=$SECONDS
  else
    echo "==> Token refresh failed — re-logging in ..."
    _login
  fi
}

# Call before each batch: refreshes only when the token is approaching expiry.
# This avoids hammering the rate-limited /auth/refresh endpoint and prevents
# unnecessary token rotation that would invalidate other admin sessions.
_maybe_refresh_token() {
  if [ $((SECONDS - TOKEN_ISSUED_AT)) -ge "$TOKEN_TTL_SECS" ]; then
    _do_refresh
  fi
}

echo "==> Logging in as admin ($ADMIN_EMAIL) ..."
_login

if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> DRY-RUN mode — running first batch only, not looping"
fi

echo "==> Starting citation intent backfill for user: $TARGET_USER_ID"
echo "    Batch size: $BATCH_SIZE"
echo ""

# ---------------------------------------------------------------------------
# Cursor loop
# ---------------------------------------------------------------------------

CURSOR=""
BATCH=0
TOTAL_PROCESSED=0
TOTAL_SKIPPED=0
TOTAL_FAILED=0

while true; do
  BATCH=$((BATCH + 1))

  # Build query string
  QS="target_user_id=${TARGET_USER_ID}&batch_size=${BATCH_SIZE}"
  if [ -n "$CURSOR" ]; then
    QS="${QS}&cursor=${CURSOR}"
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    QS="${QS}&dry_run=true"
  fi

  # Refresh the access token only when it is approaching expiry.
  _maybe_refresh_token

  echo -n "Batch $BATCH ... "

  RESP=$(curl -sf -X POST "$API/admin/backfill/citation-intents?${QS}" \
    -H "Authorization: Bearer $ADMIN_TOKEN")

  PROCESSED=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['processed'])")
  SKIPPED=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['skipped_no_raw_text'])")
  FAILED=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['failed'])")
  REMAINING=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['remaining'])")
  NEXT_CURSOR=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('next_cursor') or '')")

  TOTAL_PROCESSED=$((TOTAL_PROCESSED + PROCESSED))
  TOTAL_SKIPPED=$((TOTAL_SKIPPED + SKIPPED))
  TOTAL_FAILED=$((TOTAL_FAILED + FAILED))

  echo "processed=$PROCESSED skipped=$SKIPPED failed=$FAILED remaining=$REMAINING"

  # --dry-run: stop after the first batch
  if [ "$DRY_RUN" -eq 1 ]; then
    echo ""
    echo "==> DRY-RUN complete — stopped after first batch."
    echo "    Remaining: $REMAINING papers would still be processed."
    echo "    Re-run without --dry-run to backfill all."
    break
  fi

  if [ "$REMAINING" -eq 0 ] || [ -z "$NEXT_CURSOR" ] || [ "$PROCESSED" -eq 0 ]; then
    break
  fi

  CURSOR="$NEXT_CURSOR"
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "==> Done."
echo "    Total processed : $TOTAL_PROCESSED"
echo "    Total skipped   : $TOTAL_SKIPPED (no cached raw_text)"
echo "    Total failed    : $TOTAL_FAILED"
