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
# single token.  We decode the JWT exp claim after each login so re-auth
# timing tracks the server's actual TTL — not a hardcoded local constant
# that can drift from KLEMMA_ACCESS_TOKEN_EXPIRE_MINUTES.
# ---------------------------------------------------------------------------

ADMIN_TOKEN=""
TOKEN_REFRESH_AFTER=0  # Unix timestamp: re-login when now >= this value

# Decode the `exp` Unix timestamp from a JWT access token (base64url payload).
# Prints 0 on any decode failure so _maybe_refresh_token still triggers a login.
_get_token_exp() {
  local payload
  payload=$(echo "$1" | cut -d. -f2)
  python3 - "$payload" 2>/dev/null <<'PYEOF' || echo 0
import sys, json, base64
p = sys.argv[1]
p += '=' * ((4 - len(p) % 4) % 4)
try:
    print(json.loads(base64.b64decode(p.replace('-','+').replace('_','/')))['exp'])
except Exception:
    print(0)
PYEOF
}

_login() {
  local resp issued exp lifetime
  resp=$(curl -sf -X POST "$API/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$ADMIN_EMAIL\", \"password\": \"$ADMIN_PASS\"}") || {
      echo "ERROR: Login failed"
      exit 1
    }
  ADMIN_TOKEN=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
  issued=$(date +%s)
  exp=$(_get_token_exp "$ADMIN_TOKEN")
  if [ "$exp" -le 0 ]; then
    # Decode failed — set refresh point to now so the next batch triggers
    # a fresh login (conservative; avoids a 401 mid-run).
    TOKEN_REFRESH_AFTER=$issued
  else
    lifetime=$((exp - issued))
    # Re-login at 90% of the actual token lifetime so the buffer is always
    # proportional.  A fixed buffer (e.g. 60 s) breaks when the server is
    # configured with a short TTL: a 1-min token is already inside a 60 s
    # window the moment _login() returns, causing a login on every batch.
    TOKEN_REFRESH_AFTER=$((issued + lifetime * 9 / 10))
  fi
}

# Call before each batch: re-authenticates only when the token has consumed
# 90% of its lifetime.  We deliberately re-login rather than calling
# /auth/refresh — the refresh endpoint revokes all refresh tokens for the
# user, which would silently log out every other admin session.
# /auth/login adds a new token without revoking existing ones.
_maybe_refresh_token() {
  local now
  now=$(date +%s)
  if [ "$now" -ge "$TOKEN_REFRESH_AFTER" ]; then
    echo "==> Re-authenticating (access token at 90% of lifetime) ..."
    _login
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
