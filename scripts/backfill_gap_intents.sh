#!/usr/bin/env bash
# Backfill citation intents for a user's citation graph entries.
#
# Calls POST /admin/backfill/citation-intents in a cursor loop until all
# papers are processed. Requires admin credentials.
#
# Usage:
#   ./scripts/backfill_gap_intents.sh <target_user_id> [batch_size]
#
# Environment:
#   KLEMMA_API_URL       — API base URL (default: https://litresearch.ru/api)
#   KLEMMA_ADMIN_EMAIL   — Admin account email (required)
#   KLEMMA_ADMIN_PASSWORD — Admin account password (required)
#
# Example:
#   KLEMMA_ADMIN_EMAIL=admin@example.com \
#   KLEMMA_ADMIN_PASSWORD=secret \
#   ./scripts/backfill_gap_intents.sh user-uuid-here 20

set -euo pipefail

API="${KLEMMA_API_URL:-https://litresearch.ru/api}"
ADMIN_EMAIL="${KLEMMA_ADMIN_EMAIL:?Set KLEMMA_ADMIN_EMAIL}"
ADMIN_PASS="${KLEMMA_ADMIN_PASSWORD:?Set KLEMMA_ADMIN_PASSWORD}"
TARGET_USER_ID="${1:?Usage: backfill_gap_intents.sh <target_user_id> [batch_size]}"
BATCH_SIZE="${2:-20}"

echo "==> Logging in as admin ($ADMIN_EMAIL) ..."
ADMIN_TOKEN=$(curl -sf -X POST "$API/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$ADMIN_EMAIL\", \"password\": \"$ADMIN_PASS\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "==> Starting citation intent backfill for user: $TARGET_USER_ID"
echo "    Batch size: $BATCH_SIZE"
echo ""

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

  if [ "$REMAINING" -eq 0 ] || [ -z "$NEXT_CURSOR" ] || [ "$PROCESSED" -eq 0 ]; then
    break
  fi

  CURSOR="$NEXT_CURSOR"
done

echo ""
echo "==> Done."
echo "    Total processed : $TOTAL_PROCESSED"
echo "    Total skipped   : $TOTAL_SKIPPED (no cached raw_text)"
echo "    Total failed    : $TOTAL_FAILED"
