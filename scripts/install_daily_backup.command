#!/bin/zsh
# Double-click once to schedule a daily database backup at 03:15 (macOS LaunchAgent).
# Run with --remove to uninstall.  Logs: ~/Library/Logs/LibraryKeeper-backup.log
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.librarykeeper.backup"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
if [ "$1" = "--remove" ]; then rm -f "$PLIST"; echo "أُلغيت النسخة اليومية."; exit 0; fi
"$ROOT/scripts/ensure_venv.sh" || exit 1
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$ROOT/backend/venv/bin/python</string>
    <string>$ROOT/scripts/backup_db.py</string>
    <string>--reason</string><string>daily</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/LibraryKeeper-backup.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/LibraryKeeper-backup.log</string>
</dict></plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "✓ النسخة اليومية مفعّلة (كل يوم 3:15 صباحًا)."
echo "تجربة الآن…"
"$ROOT/backend/venv/bin/python" "$ROOT/scripts/backup_db.py" --reason install-test
