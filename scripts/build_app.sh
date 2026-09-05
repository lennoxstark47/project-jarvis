#!/usr/bin/env bash
# Builds a minimal Jarvis.app wrapper so macOS attributes mic/camera TCC
# prompts (and launchd login-item identity) to "Jarvis" instead of Terminal
# or a bare python3 binary.
#
# This does NOT freeze Python or its dependencies (that's what py2app tried
# to do here and failed at: this machine's Anaconda-based interpreter hit a
# missing libffi.8.dylib at runtime, and cv2/sounddevice's native extensions
# weren't even being picked up). Instead this is a thin, native-looking shell
# around the existing .venv — the real python3 -m jarvis.main still runs
# normally, with all deps resolving the way they already do in development.
# It also scales better: every later phase adds heavier native deps (Whisper,
# MediaPipe, Playwright's bundled browsers), each of which py2app would need
# to be taught to freeze correctly; a launcher script doesn't care what's
# installed in the venv.
#
# Usage: scripts/build_app.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$PROJECT_ROOT/dist/Jarvis.app"
CONTENTS="$APP/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RESOURCES_DIR="$CONTENTS/Resources"

if [[ ! -x "$PROJECT_ROOT/.venv/bin/python3" ]]; then
    echo "error: $PROJECT_ROOT/.venv not found." >&2
    echo "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

rm -rf "$APP"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Jarvis</string>
    <key>CFBundleDisplayName</key>
    <string>Jarvis</string>
    <key>CFBundleIdentifier</key>
    <string>com.jarvis.agent</string>
    <key>CFBundleExecutable</key>
    <string>Jarvis</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Jarvis needs the microphone to hear your voice commands.</string>
    <key>NSCameraUsageDescription</key>
    <string>Jarvis needs the camera to see your hand gestures.</string>
</dict>
</plist>
PLIST

cat > "$MACOS_DIR/Jarvis" <<LAUNCHER
#!/usr/bin/env bash
# Thin launcher: runs Jarvis from its real .venv so all deps resolve normally.
exec "$PROJECT_ROOT/.venv/bin/python3" "$PROJECT_ROOT/run.py"
LAUNCHER
chmod +x "$MACOS_DIR/Jarvis"

echo "Built $APP"
echo "Open it once by hand first (so you can click Allow on the permission"
echo "prompts) with: open \"$APP\""
