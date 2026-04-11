from __future__ import annotations

from setuptools import setup


APP = ["lyricsx_style_app.py"]
DATA_FILES = [
    "lyrics_state.py",
    "applyrx_state.py",
    "main.py",
    "apple_music_ttml.py",
]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["AppKit", "Foundation", "objc"],
    "includes": ["PyObjCTools.AppHelper", "urllib.request", "urllib.parse", "sqlite3", "json", "subprocess"],
    "plist": {
        "CFBundleName": "Applyrx",
        "CFBundleDisplayName": "Applyrx",
        "CFBundleIdentifier": "local.applyrx.lyrics",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSAppleEventsUsageDescription": "Applyrx reads the current Apple Music track and playback position to synchronize lyrics.",
    },
}


setup(
    app=APP,
    name="Applyrx",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
