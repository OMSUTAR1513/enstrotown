#!/data/data/com.termux/files/usr/bin/bash
# Restart only after an unexpected crash, with bounded exponential backoff.
cd "$(dirname "$0")" || exit 1
if [ -d .venv ]; then
  . .venv/bin/activate
fi
if [ -z "${BOT_TOKEN:-}" ]; then
  echo "Set your BotFather token first: export BOT_TOKEN='YOUR_BOTFATHER_TOKEN'"
  exit 1
fi

stop_requested=0
trap 'stop_requested=1' INT TERM
retry_count=0

while true; do
  started_at=$SECONDS
  echo "[$(date)] Starting Telegram listing bot..."
  python bot.py
  status=$?
  runtime=$((SECONDS - started_at))

  if [ "$stop_requested" -eq 1 ]; then
    echo "Bot stopped by user."
    exit 0
  fi

  if [ "$status" -eq 0 ]; then
    echo "[$(date)] Bot exited normally. Not restarting automatically."
    exit 0
  fi

  # A process that ran for at least five minutes is treated as recovered;
  # begin the retry backoff again at five seconds after a later crash.
  if [ "$runtime" -ge 300 ]; then
    retry_count=0
  fi
  if [ "$retry_count" -lt 7 ]; then
    retry_count=$((retry_count + 1))
  fi

  case "$retry_count" in
    1) delay=5 ;;
    2) delay=10 ;;
    3) delay=20 ;;
    4) delay=40 ;;
    5) delay=80 ;;
    6) delay=160 ;;
    *) delay=300 ;;
  esac

  echo "[$(date)] Bot crashed with exit status $status after ${runtime}s."
  echo "See the traceback above. Restart attempt $retry_count; waiting ${delay}s. Press Ctrl+C to stop."
  waited=0
  while [ "$waited" -lt "$delay" ]; do
    if [ "$stop_requested" -eq 1 ]; then
      echo "Restart cancelled by user."
      exit 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
done
