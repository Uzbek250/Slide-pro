#!/usr/bin/env bash
# Backend (FastAPI) ni ishga tushirish.
#   ./scripts/web.sh            # 8003 portda
#   PORT=9000 ./scripts/web.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

PORT="${PORT:-8003}"
HOST="${HOST:-127.0.0.1}"
WORKERS="${WORKERS:-1}"

echo "Slide backend → http://${HOST}:${PORT}"
echo "  navbat: ${MAX_CONCURRENT_JOBS:-2} bir vaqtda, ${MAX_QUEUE:-12} kutish"
echo "  limit: ${RATE_PER_MINUTE:-1} ta/daqiqa user, ${RATE_GLOBAL_PER_MINUTE:-3} ta/daqiqa servis, ${RATE_PER_DAY:-20} ta/kun user"

exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
