#!/bin/zsh
# Stops EmailManager started with start.sh. (If you enabled ./autostart.sh on, use ./autostart.sh off instead.)
cd "$(dirname "$0")"
if [ -f data/emailmanager.pid ] && kill "$(cat data/emailmanager.pid)" 2>/dev/null; then
  rm -f data/emailmanager.pid; echo "Stopped."
else
  PID=$(lsof -ti tcp:5055 -sTCP:LISTEN 2>/dev/null)
  [ -n "$PID" ] && kill $PID && echo "Stopped." || echo "Not running."
fi
