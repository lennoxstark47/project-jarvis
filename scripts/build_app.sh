#!/usr/bin/env bash
# Builds a minimal Jarvis.app wrapper so macOS attributes mic/camera TCC
# prompts (and launchd login-item identity) to "Jarvis" instead of Terminal
# or a bare python3 binary — and so the menu bar status item itself is
# actually granted by Control Center.
#
# This does NOT freeze Python or its dependencies (that's what py2app tried
# to do here and failed at: this machine's Anaconda-based interpreter hit a
# missing libffi.8.dylib at runtime, and cv2/sounddevice's native extensions
# weren't even being picked up).
#
# An earlier version of this script made Contents/MacOS/Jarvis a shell script
# that `exec`'d the real python3 interpreter from .venv/bin. That got mic/
# camera permission identity right (TCC tolerates it), but the menu bar icon
# never appeared: Control Center's status-item "scene" service refused every
# request with "scene activation failed ... XPC error", because the actual
# running binary (the exec'd python3, physically living outside the bundle)
# had no Info.plist bound to its code signature (`codesign -dv` showed
# "Info.plist=not bound"). Control Center requires that binding.
#
# Fix: copy the real interpreter binary itself into Contents/MacOS/Jarvis
# (not a wrapper script — exec() always swaps out the running image's
# identity, so a script that execs elsewhere can never carry the binding),
# auto-run our code via a sitecustomize.py hook instead of a CLI argument
# (so a plain double-click / `open` with no --args still works), and re-sign
# the whole bundle ad hoc so Info.plist gets bound to that copied binary.
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

REAL_PYTHON="$(cd "$PROJECT_ROOT" && .venv/bin/python3 -c 'import sys; print(sys.executable)')"
# Copying the raw interpreter binary loses venv context: venvs work by the
# original .venv/bin/python3 finding a pyvenv.cfg next to itself, which a
# copy placed inside Contents/MacOS/ has no access to (confirmed by testing:
# the copied binary raised ModuleNotFoundError for rumps until this was
# added). Querying site-packages through the *real* venv python3 first,
# then handing that path to the copy via PYTHONPATH, sidesteps needing to
# replicate pyvenv.cfg detection.
VENV_SITE_PACKAGES="$(cd "$PROJECT_ROOT" && .venv/bin/python3 -c 'import site; print(site.getsitepackages()[0])')"

rm -rf "$APP"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

cp "$REAL_PYTHON" "$MACOS_DIR/Jarvis"
chmod +x "$MACOS_DIR/Jarvis"

cat > "$RESOURCES_DIR/sitecustomize.py" <<PYEOF
# Auto-run hook: Python's site module imports this unconditionally on
# startup (unless launched with -S), regardless of whether a script
# argument was given — which is what lets Contents/MacOS/Jarvis (a plain
# copy of the python3 binary) launch straight into Jarvis on a bare
# double-click, with no CLI arguments involved.
import os
import runpy
import sys

script = os.environ.get("JARVIS_RUN_SCRIPT")
if script:
    sys.argv = [script]
    runpy.run_path(script, run_name="__main__")
PYEOF

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
    <key>LSEnvironment</key>
    <dict>
        <key>JARVIS_RUN_SCRIPT</key>
        <string>$PROJECT_ROOT/run.py</string>
        <key>PYTHONPATH</key>
        <string>$RESOURCES_DIR:$VENV_SITE_PACKAGES</string>
    </dict>
</dict>
</plist>
PLIST

# Ad-hoc re-sign the bundle so the copied interpreter's code signature gets
# Info.plist bound to it — this is the actual thing Control Center checks
# before granting a menu bar status item.
codesign --force --deep -s - "$APP"

echo "Built $APP"
echo "Open it once by hand first (so you can click Allow on the permission"
echo "prompts) with: open \"$APP\""
