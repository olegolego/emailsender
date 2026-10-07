#!/bin/zsh
# ./autostart.sh on   - start EmailManager automatically at login and restart it if it crashes
# ./autostart.sh off  - remove that
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.eigenreach.emailmanager.plist"
if [ "$1" = "off" ]; then
  launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null; rm -f "$PLIST"; echo "Autostart removed."; exit 0
fi
mkdir -p "$DIR/data" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.eigenreach.emailmanager</string>
  <key>ProgramArguments</key><array><string>$DIR/.venv/bin/python</string><string>$DIR/run.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>4096</integer></dict>
  <key>StandardOutPath</key><string>$DIR/data/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/server.log</string>
</dict></plist>
PL
"$DIR/stop.sh" >/dev/null 2>&1; sleep 1
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
launchctl bootstrap gui/$(id -u) "$PLIST" && echo "EmailManager now starts at login: http://127.0.0.1:5055"
