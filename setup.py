from __future__ import annotations

from setuptools import setup


APP = ["applyrx_ui.py"]
DATA_FILES = [
    "lyrics_state.py",
    "applyrx_state.py",
    "main.py",
    "apple_music_ttml.py",
]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "assets/Applyrx.icns",
    "packages": [
        "AppKit", "Foundation", "objc",
        "rich",
        "requests",
        "urllib3",
        "certifi",
        "charset_normalizer",
        "idna",
        "PIL",
    ],
    "includes": [
        "PyObjCTools.AppHelper",
        "urllib.request", "urllib.parse",
        "sqlite3", "json", "subprocess",
        "plistlib", "shutil", "tempfile", "re", "time",
    ],
    "plist": {
        "CFBundleName": "Applyrx",
        "CFBundleDisplayName": "Applyrx",
        "CFBundleIdentifier": "local.applyrx.lyrics",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSAppleEventsUsageDescription": "Applyrx reads the current Apple Music track and playback position to synchronize lyrics.",
        "NSLoginItemsUsageDescription": "Applyrx can launch at login to keep lyrics always available.",
    },
}


setup(
    app=APP,
    name="Applyrx",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
