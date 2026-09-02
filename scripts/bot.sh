#!/usr/bin/env bash
# Telegram botni ishga tushirish (backend allaqachon ishlab turishi kerak).
#   ./scripts/bot.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "XATO: TELEGRAM_BOT_TOKEN .env faylida yo'q" >&2
  exit 1
fi

API_BASE="${API_BASE:-http://127.0.0.1:8003}"
export API_BASE

if ! curl -sf "${API_BASE}/health" >/dev/null; then
  echo "XATO: backend javob bermayapti (${API_BASE}/health). Avval ./scripts/web.sh ni ishga tushiring." >&2
  exit 1
fi

echo "Telegram bot ishga tushmoqda (API_BASE=${API_BASE})"
exec .venv/bin/python -m bot.telegram_bot
