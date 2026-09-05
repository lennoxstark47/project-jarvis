#!/usr/bin/env bash
# Removes the Jarvis launchd user agent installed by install_launch_agent.sh.
set -euo pipefail

PLIST_LABEL="com.jarvis.agent"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
rm -f "$PLIST_DEST"

echo "Removed $PLIST_DEST. Jarvis will no longer start automatically at login."
