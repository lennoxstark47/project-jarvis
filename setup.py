"""
py2app build script — packages Jarvis as a real .app bundle so macOS attributes
microphone/camera permission prompts to "Jarvis" itself (via the usage
descriptions below) instead of to Terminal or a bare `python3` binary.

    source .venv/bin/activate
    pip install -r requirements-build.txt
    python3 setup.py py2app

Built app lands in dist/Jarvis.app. This is the polish step behind the
`launchd` agent in scripts/ — deferred until the plain `python3 run.py` path
(see run.py) has already proven the mic/camera logic works, since bundling
adds its own set of things that can go wrong independent of that logic.
"""
from setuptools import setup

APP = ["src/jarvis/main.py"]

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Jarvis",
        "CFBundleDisplayName": "Jarvis",
        "CFBundleIdentifier": "com.jarvis.agent",
        "LSUIElement": True,  # menu bar only — no Dock icon, no normal window
        "NSMicrophoneUsageDescription": "Jarvis needs the microphone to hear your voice commands.",
        "NSCameraUsageDescription": "Jarvis needs the camera to see your hand gestures.",
    },
    "packages": ["rumps"],
}

setup(
    app=APP,
    name="Jarvis",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
