#!/usr/bin/env bash
# Create a new CiteQ user via API.
# Usage: ./scripts/create_user.sh <email> <password> [name] [token_amount]
#
# Examples:
#   ./scripts/create_user.sh user@example.com MyPass123
#   ./scripts/create_user.sh user@example.com MyPass123 "Ivan Petrov" 100000

set -euo pipefail

API="${CITEQ_API_URL:-https://litresearch.ru/api}"
ADMIN_EMAIL="${CITEQ_ADMIN_EMAIL:-}"
ADMIN_PASS="${CITEQ_ADMIN_PASSWORD:-}"

EMAIL="${1:?Usage: create_user.sh <email> <password> [name] [token_amount]}"
PASSWORD="${2:?Usage: create_user.sh <email> <password> [name] [token_amount]}"
NAME="${3:-}"
TOKENS="${4:-50000}"

echo "==> Registering $EMAIL ..."
RESP=$(curl -sf -X POST "$API/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\", \"name\": \"$NAME\"}")

USER_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['user_id'])")
echo "    user_id: $USER_ID"

# Grant tokens if admin credentials are configured
if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASS" ]; then
  echo "==> Logging in as admin ($ADMIN_EMAIL) ..."
  ADMIN_TOKEN=$(curl -sf -X POST "$API/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$ADMIN_EMAIL\", \"password\": \"$ADMIN_PASS\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

  echo "==> Granting $TOKENS tokens ..."
  curl -sf -X POST "$API/usage/grant" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"user_id\": \"$USER_ID\", \"amount\": $TOKENS}" \
    | python3 -m json.tool
else
  echo ""
  echo "    To grant tokens, set CITEQ_ADMIN_EMAIL and CITEQ_ADMIN_PASSWORD, or run:"
  echo "    curl -X POST $API/usage/grant \\"
  echo "      -H 'Authorization: Bearer <admin_token>' \\"
  echo "      -H 'Content-Type: application/json' \\"
  echo "      -d '{\"user_id\": \"$USER_ID\", \"amount\": $TOKENS}'"
fi

echo ""
echo "Done. User can log in at https://litresearch.ru/login"
