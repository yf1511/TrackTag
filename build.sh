#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# TrackTag — macOS build script
# Produces:  dist/TrackTag.app   (runnable .app bundle)
#            dist/TrackTag.dmg   (drag-to-install disk image)
#
# Usage:
#   chmod +x build.sh
#   ./build.sh
#
# Optional — sign the app (requires Apple Developer account):
#   CODESIGN_ID="Developer ID Application: Your Name (TEAMID)" ./build.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

APP_NAME="TrackTag"
DMG_NAME="${APP_NAME}.dmg"
DIST_DIR="dist"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"

# ── Read version from version.py ──────────────────────────────────────────────
VERSION=$(python3 -c "from version import __version__; print(__version__)")
echo "▶  Building ${APP_NAME} v${VERSION}"

# ── 1. Clean previous build ───────────────────────────────────────────────────
echo "▶  Cleaning previous build…"
rm -rf build dist __pycache__

# ── 2. Run PyInstaller ────────────────────────────────────────────────────────
echo "▶  Running PyInstaller…"
python3 -m PyInstaller tracktag.spec --noconfirm

echo "✓  App bundle created at ${APP_PATH}"

# ── 3. Ad-hoc code signing (no Apple Developer account needed) ───────────────
#    This lets macOS run the app without "damaged / can't be opened" errors.
#    For real distribution: set CODESIGN_ID to your Developer ID certificate.
if [ -n "${CODESIGN_ID}" ]; then
    echo "▶  Signing with: ${CODESIGN_ID}"
    codesign --deep --force --options runtime \
        --sign "${CODESIGN_ID}" "${APP_PATH}"
    echo "✓  Signed."
else
    echo "▶  Ad-hoc signing (no Developer ID)…"
    codesign --deep --force --sign - "${APP_PATH}"
    echo "✓  Ad-hoc signed."
fi

# ── 4. Remove quarantine attribute (helps when testing locally) ───────────────
xattr -cr "${APP_PATH}" 2>/dev/null || true

# ── 5. Create DMG ─────────────────────────────────────────────────────────────
echo "▶  Creating DMG…"

# Use create-dmg if available (nicer), fall back to hdiutil
if command -v create-dmg &>/dev/null; then
    create-dmg \
        --volname "${APP_NAME} ${VERSION}" \
        --volicon "assets/app-icon.png" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "${APP_NAME}.app" 150 185 \
        --hide-extension "${APP_NAME}.app" \
        --app-drop-link 450 185 \
        --no-internet-enable \
        "${DMG_PATH}" \
        "${APP_PATH}"
else
    # Fallback: plain DMG via hdiutil (always available on macOS)
    STAGING="/tmp/${APP_NAME}_dmg_staging"
    rm -rf "${STAGING}" && mkdir -p "${STAGING}"
    cp -R "${APP_PATH}" "${STAGING}/"
    ln -s /Applications "${STAGING}/Applications"

    hdiutil create \
        -volname "${APP_NAME} ${VERSION}" \
        -srcfolder "${STAGING}" \
        -ov -format UDZO \
        "${DMG_PATH}"

    rm -rf "${STAGING}"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓  Build complete!                                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  .app  →  ${APP_PATH}"
echo "║  .dmg  →  ${DMG_PATH}"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Zum Senden an Freunde:                                      ║"
echo "║    Die .dmg Datei verschicken.                               ║"
echo "║    Empfänger: DMG öffnen → TrackTag in Applications ziehen.  ║"
echo "║                                                              ║"
echo "║  Hinweis: Beim ersten Start muss der Empfänger              ║"
echo "║    Rechtsklick → Öffnen wählen (macOS Gatekeeper).           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
