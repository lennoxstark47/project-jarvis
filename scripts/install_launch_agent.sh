#!/usr/bin/env bash
# Installs Jarvis as a launchd user agent so it starts at login.
#
# Prerequisite: build the app bundle first — scripts/build_app.sh
#
# Usage: scripts/install_launch_agent.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BINARY="$PROJECT_ROOT/dist/Jarvis.app/Contents/MacOS/Jarvis"
PLIST_LABEL="com.jarvis.agent"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

if [[ ! -x "$APP_BINARY" ]]; then
    echo "error: $APP_BINARY not found." >&2
    echo "Build it first with: scripts/build_app.sh" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

sed \
    -e "s|__JARVIS_APP_BINARY__|$APP_BINARY|g" \
    -e "s|__JARVIS_PROJECT_ROOT__|$PROJECT_ROOT|g" \
    "$PROJECT_ROOT/scripts/com.jarvis.agent.plist.template" > "$PLIST_DEST"

# Unload any previous copy before loading the fresh one (bootout fails
# harmlessly if it wasn't loaded yet).
launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"

echo "Installed and loaded $PLIST_DEST"
echo "Jarvis will now also start automatically at login."
echo "Check logs/launchd.out.log and logs/launchd.err.log if it doesn't appear in the menu bar."
