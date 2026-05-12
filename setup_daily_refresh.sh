#!/bin/bash
# setup_daily_refresh.sh — schedule daily_refresh.py to run automatically.
#
# macOS: installs a launchd .plist at ~/Library/LaunchAgents/
# Linux: prints the crontab line to add manually
#
# Schedule: every weekday (Mon-Fri) at 4:30 PM IST = local time
# (Run on a Mac/Linux box in IST. If your machine is in another timezone,
#  edit the Hour value below.)
#
# Usage:
#   ./setup_daily_refresh.sh           # install the schedule
#   ./setup_daily_refresh.sh --remove  # uninstall it
#   ./setup_daily_refresh.sh --status  # check if it's installed
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
REFRESH_SCRIPT="$PROJECT_DIR/daily_refresh.py"

LABEL="com.breakoutlab.refresh"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"

# ---- Verify environment ----
if [ ! -f "$PYTHON_BIN" ]; then
  echo "❌ Python venv not found at $PYTHON_BIN"
  echo "   Run ./setup.sh first to create the venv."
  exit 1
fi
if [ ! -f "$REFRESH_SCRIPT" ]; then
  echo "❌ daily_refresh.py not found at $REFRESH_SCRIPT"
  exit 1
fi

# ---- Action: --remove ----
if [ "${1:-}" = "--remove" ]; then
  if [[ "$OSTYPE" == "darwin"* ]]; then
    if [ -f "$PLIST_PATH" ]; then
      launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
      rm -f "$PLIST_PATH"
      echo "✅ Daily refresh schedule removed."
    else
      echo "ℹ️  No schedule installed."
    fi
  else
    echo "Linux: remove the line from crontab manually with: crontab -e"
  fi
  exit 0
fi

# ---- Action: --status ----
if [ "${1:-}" = "--status" ]; then
  if [[ "$OSTYPE" == "darwin"* ]]; then
    if launchctl list | grep -q "$LABEL"; then
      echo "✅ Daily refresh is scheduled (launchd)"
      launchctl list | grep "$LABEL"
    else
      echo "❌ Daily refresh is NOT scheduled. Run without flags to install."
    fi
  else
    echo "Linux: check 'crontab -l' for a line containing daily_refresh.py"
  fi
  exit 0
fi

# ---- Action: install (default) ----
mkdir -p "$PROJECT_DIR/logs"

if [[ "$OSTYPE" == "darwin"* ]]; then
  # macOS: launchd .plist
  mkdir -p "$HOME/Library/LaunchAgents"

  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${REFRESH_SCRIPT}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer></dict>
    </array>

    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/logs/launchd_refresh.out</string>

    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/logs/launchd_refresh.err</string>

    <key>RunAtLoad</key>
    <false/>

    <!-- If machine was asleep at scheduled time, run on next wake. -->
    <key>StartCalendarIntervalMissed</key>
    <true/>
</dict>
</plist>
EOF

  # Reload (idempotent — unload first if exists)
  launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
  launchctl load -w "$PLIST_PATH"

  echo "✅ Daily refresh scheduled via launchd"
  echo "   Plist: $PLIST_PATH"
  echo "   Schedule: Mon-Fri at 16:30 (4:30 PM) local time"
  echo "   Logs: $PROJECT_DIR/logs/launchd_refresh.{out,err}"
  echo ""
  echo "   Verify:  ./setup_daily_refresh.sh --status"
  echo "   Remove:  ./setup_daily_refresh.sh --remove"
  echo ""
  echo "   ⚠️  Edit the .plist Hour value if your Mac is not in IST."
else
  # Linux: print the crontab line for manual installation
  CRON_LINE="30 16 * * 1-5 cd ${PROJECT_DIR} && ${PYTHON_BIN} ${REFRESH_SCRIPT} >> ${PROJECT_DIR}/logs/cron_refresh.log 2>&1"
  echo "Linux detected. Add this line to your crontab (run 'crontab -e'):"
  echo ""
  echo "  $CRON_LINE"
  echo ""
  echo "This runs at 4:30 PM local time, Mon-Fri."
fi
