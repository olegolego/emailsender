#!/bin/zsh
# Starts EmailManager at http://127.0.0.1:5055 (creates the Python environment on first run).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  PY=$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3)
  "$PY" -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
if curl -s -o /dev/null http://127.0.0.1:5055/api/state; then
  echo "EmailManager is already running."
else
  mkdir -p data; nohup "$PWD/.venv/bin/python" "$PWD/run.py" >> data/server.log 2>&1 & echo $! > data/emailmanager.pid
  sleep 2
fi
[ -z "$NO_OPEN" ] && open "http://127.0.0.1:5055"
