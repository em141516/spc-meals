#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_NAME="com.spc.meals.plist"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
PYTHON_PATH="/opt/homebrew/bin/python3"
CONFIG="$SCRIPT_DIR/config.json"

echo "=== SPC Meals Notifier — Install ==="

# 1. Verify Python
if [ ! -x "$PYTHON_PATH" ]; then
    DETECTED=$(command -v python3 2>/dev/null || true)
    if [ -z "$DETECTED" ]; then
        echo "ERROR: python3 not found. Install Homebrew Python: brew install python3"
        exit 1
    fi
    echo "WARNING: $PYTHON_PATH not found. Using python3 at: $DETECTED"
    PYTHON_PATH="$DETECTED"
fi
echo "Python: $PYTHON_PATH ($($PYTHON_PATH --version))"

# 2. Install optional packages (non-fatal — scraper uses stdlib only)
echo "Installing Python packages (optional)..."
"$PYTHON_PATH" -m pip install --quiet beautifulsoup4 requests 2>/dev/null || \
    echo "  (Skipped — system Python is externally managed. Scraper works without them.)"

# 3. Create config if missing
if [ ! -f "$CONFIG" ]; then
    cat > "$CONFIG" <<EOF
{
  "ntfy_topic_prefix": "spc-meals-yourname",
  "ntfy_base_url": "https://ntfy.sh",
  "meals_url": "https://www.spc.ox.ac.uk/student-life/living-at-st-peters/meals",
  "log_file": "$SCRIPT_DIR/meals_notifier.log",
  "request_timeout_seconds": 15
}
EOF
fi

# Check if topic is still the placeholder
if grep -q "REPLACE_ME" "$CONFIG"; then
    echo ""
    echo "ACTION REQUIRED: Set your ntfy topic in config.json"
    echo "  1. Open the ntfy app and subscribe to a unique topic name (e.g. spc-meals-abc123)"
    echo "  2. Edit $CONFIG and replace 'spc-meals-REPLACE_ME' with your topic"
    echo "  3. Re-run this script, or run: $PYTHON_PATH $SCRIPT_DIR/scraper.py"
    echo ""
fi

# 4. Unload existing agent if loaded
MACOS_MAJOR=$(sw_vers -productVersion | cut -d. -f1)
if launchctl list com.spc.meals &>/dev/null; then
    echo "Unloading existing LaunchAgent..."
    if [ "$MACOS_MAJOR" -ge 13 ]; then
        launchctl bootout "gui/$(id -u)" "$PLIST_DEST" 2>/dev/null || true
    else
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
    fi
fi

# 5. Copy plist, substituting __SCRIPT_DIR__ and __PYTHON__ placeholders
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__SCRIPT_DIR__|$SCRIPT_DIR|g" \
    -e "s|__PYTHON__|$PYTHON_PATH|g" \
    "$PLIST_SRC" > "$PLIST_DEST"
echo "Installed plist to: $PLIST_DEST"

# 6. Load the agent
if [ "$MACOS_MAJOR" -ge 13 ]; then
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
else
    launchctl load "$PLIST_DEST"
fi

# 7. Verify
sleep 1
if launchctl list com.spc.meals &>/dev/null; then
    echo "LaunchAgent loaded successfully."
else
    echo "WARNING: Could not verify LaunchAgent. Check: launchctl list com.spc.meals"
fi

echo ""
echo "=== Setup complete ==="
echo "The notifier will run daily at 7:00 AM."
echo ""
echo "To test immediately:"
echo "  $PYTHON_PATH $SCRIPT_DIR/scraper.py"
echo ""
echo "Log files:"
echo "  App log:    $SCRIPT_DIR/meals_notifier.log"
echo "  launchd:    $SCRIPT_DIR/launchd_stderr.log"
