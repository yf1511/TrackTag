# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for TrackTag
# Run:  pyinstaller tracktag.spec
#

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# ── Collect all data/binaries for qtawesome (fonts + charmap JSON) ─────────────
qta_datas, qta_binaries, qta_hiddenimports = collect_all("qtawesome")

# ── Collect PyQt6 plugins ───────────────────────────────────────────────────────
qt_datas, qt_binaries, qt_hidden = collect_all("PyQt6")

# ── Collect mutagen ─────────────────────────────────────────────────────────────
mutagen_datas, mutagen_binaries, mutagen_hidden = collect_all("mutagen")


a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=qta_binaries + qt_binaries + mutagen_binaries,
    datas=[
        ("assets",  "assets"),      # app icon
        ("version.py", "."),        # version file at root
    ] + qta_datas + mutagen_datas,
    hiddenimports=[
        "PyQt6.sip",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "qtawesome",
        "mutagen",
        "mutagen.mp3",
        "mutagen.flac",
        "mutagen.mp4",
        "mutagen.aiff",
        "mutagen.wave",
        "mutagen.id3",
        "mutagen._util",
        "mutagen._tags",
        "PIL",
        "PIL.Image",
        "PIL.ImageFile",
        "urllib.request",
        "urllib.error",
        "json",
        "subprocess",
        "app.main_window",
        "app.audio_handler",
        "app.cover_search",
        "app.updater",
    ] + qta_hiddenimports + qt_hidden + mutagen_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "test", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TrackTag",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,     # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TrackTag",
)

app = BUNDLE(
    coll,
    name="TrackTag.app",
    icon="assets/app-icon.png",
    bundle_identifier="com.tracktag.app",
    version="1.0.7",
    info_plist={
        "CFBundleName":               "TrackTag",
        "CFBundleDisplayName":        "TrackTag",
        "CFBundleShortVersionString": "1.0.7",
        "CFBundleVersion":            "1.0.7",
        "NSHighResolutionCapable":    True,
        "NSHumanReadableCopyright":   "© 2025 TrackTag",
        "LSApplicationCategoryType":  "public.app-category.music",
        # Allow drag-drop of files onto the Dock icon
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName":       "Audio File",
                "CFBundleTypeRole":       "Editor",
                "LSItemContentTypes":     [
                    "public.mp3",
                    "public.aiff-audio",
                    "com.apple.m4a-audio",
                    "org.xiph.flac",
                    "com.microsoft.waveform-audio",
                ],
                "CFBundleTypeExtensions": ["mp3","flac","wav","aiff","aif","m4a","mp4"],
            }
        ],
    },
)
