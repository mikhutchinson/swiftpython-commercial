#!/usr/bin/env bash
# Assemble SwiftPython Studio.app from the built Swift product.
#
# Modes:
#   (default)         dev bundle — relies on a system/Homebrew Python 3.13 at runtime.
#   --bundle-python   copy a relocatable Python + numpy/pandas/scikit-learn/scipy into
#                     the app so it runs on a machine with no Python (see bundle_python.sh).
#   --sign            codesign with Developer ID (inside-out: worker + bundled native code,
#                     then the app), hardened runtime. Required before notarization.
#   --open            launch the app when done.
#
# Notarization + DMG are separate steps (see SHIPPING.md).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="$(cd "$PKG_DIR/../.." && pwd)"     # swiftpython-commercial root (has SwiftPythonWorker)
APP_NAME="SwiftPython Studio"
APP_DIR="$PKG_DIR/build/${APP_NAME}.app"
CONFIG="release"
DO_OPEN=0; DO_SIGN=0; DO_BUNDLE_PYTHON=0
SIGN_ID="${STUDIO_CODESIGN_IDENTITY:-Developer ID Application}"

for arg in "$@"; do case "$arg" in
    --open) DO_OPEN=1 ;;
    --sign) DO_SIGN=1 ;;
    --bundle-python) DO_BUNDLE_PYTHON=1 ;;
    --debug) CONFIG="debug" ;;
    *) echo "Unknown argument: $arg" >&2; exit 64 ;;
esac; done

cd "$PKG_DIR"
echo "==> swift build -c $CONFIG (Studio + studio-cli)"
swift build -c "$CONFIG" --product Studio
swift build -c "$CONFIG" --product studio-cli
BIN_DIR="$(swift build -c "$CONFIG" --show-bin-path)"

echo "==> assembling $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BIN_DIR/Studio" "$APP_DIR/Contents/MacOS/Studio"
cp "$BIN_DIR/studio-cli" "$APP_DIR/Contents/MacOS/studio-cli"

# App icon
if [ -f "$PKG_DIR/Resources/AppIcon.icns" ]; then
    cp "$PKG_DIR/Resources/AppIcon.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
else
    echo "WARNING: Resources/AppIcon.icns not found; app will use the generic icon" >&2
fi

# Bundle the worker sidecar so PythonProcessPool auto-discovers it.
if [ -f "$REPO_DIR/SwiftPythonWorker" ]; then
    cp "$REPO_DIR/SwiftPythonWorker" "$APP_DIR/Contents/MacOS/SwiftPythonWorker"
else
    echo "WARNING: $REPO_DIR/SwiftPythonWorker not found; pool will rely on SWIFTPYTHON_WORKER_PATH" >&2
fi

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>Studio</string>
    <key>CFBundleIdentifier</key><string>dev.swiftpython.studio</string>
    <key>CFBundleName</key><string>SwiftPython Studio</string>
    <key>CFBundleDisplayName</key><string>SwiftPython Studio</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleIconName</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${STUDIO_APP_SHORT_VERSION:-0.1.0}</string>
    <key>CFBundleVersion</key><string>${STUDIO_APP_BUILD_VERSION:-1}</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
    <key>LSMinimumSystemVersion</key><string>15.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST

if [ "$DO_BUNDLE_PYTHON" -eq 1 ]; then
    echo "==> bundling Python runtime"
    "$SCRIPT_DIR/bundle_python.sh" "$APP_DIR"

    echo "==> relinking binaries to the bundled Python framework (@rpath)"
    OLD_PY="/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/Python"
    NEW_PY="@rpath/Python.framework/Versions/3.13/Python"
    for b in Studio studio-cli SwiftPythonWorker; do
        bp="$APP_DIR/Contents/MacOS/$b"
        [ -f "$bp" ] || continue
        install_name_tool -change "$OLD_PY" "$NEW_PY" "$bp" 2>/dev/null || true
        install_name_tool -add_rpath "@executable_path/../Frameworks" "$bp" 2>/dev/null || true
    done
fi

if [ "$DO_SIGN" -eq 1 ]; then
    echo "==> codesigning (Developer ID, hardened runtime, inside-out)"
    ENT="$SCRIPT_DIR/studio.entitlements"
    SCAN_DIRS=()
    [ -d "$APP_DIR/Contents/Frameworks" ] && SCAN_DIRS+=("$APP_DIR/Contents/Frameworks")
    [ -d "$APP_DIR/Contents/Resources/Python" ] && SCAN_DIRS+=("$APP_DIR/Contents/Resources/Python")

    if [ "${#SCAN_DIRS[@]}" -gt 0 ]; then
        # 1a) strip executable bits from non-Mach-O Python payloads (Gatekeeper "damaged" guard)
        echo "    normalizing non-Mach-O executable bits…"
        find "${SCAN_DIRS[@]}" -type f -perm -111 2>/dev/null | while IFS= read -r f; do
            file "$f" | grep -q 'Mach-O' || chmod -x "$f"
        done
        # 1b) sign every nested Mach-O (wheel .so, framework dylibs, the Python binary, bin/python3.13)
        echo "    signing nested Mach-O (this takes a few minutes)…"
        count=0
        while IFS= read -r -d '' f; do
            if file "$f" | grep -q 'Mach-O'; then
                codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$f"
                count=$((count + 1))
            fi
        done < <(find "${SCAN_DIRS[@]}" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -111 \) -print0 2>/dev/null)
        echo "    signed $count nested Mach-O files"
    fi

    # 2) re-seal the Python framework as a proper versioned bundle (after nested code is signed)
    if [ -d "$APP_DIR/Contents/Frameworks/Python.framework" ]; then
        codesign --force --options runtime --timestamp --sign "$SIGN_ID" \
            "$APP_DIR/Contents/Frameworks/Python.framework"
    fi

    # 3) the worker sidecar + nested CLI (re-sign after install_name_tool relink).
    #    NOTE: do NOT sign Contents/MacOS/Studio separately — sealing the .app (step 4)
    #    signs the main executable + applies entitlements. Signing it twice yields an
    #    "invalid signature" at notarization.
    codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APP_DIR/Contents/MacOS/SwiftPythonWorker"
    codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APP_DIR/Contents/MacOS/studio-cli"

    # 4) the outer app last (no --deep; seals nested code, signs the main executable + entitlements)
    codesign --force --options runtime --timestamp \
        --entitlements "$ENT" --sign "$SIGN_ID" "$APP_DIR"
    codesign --verify --strict --verbose=2 "$APP_DIR"
fi

echo "Created: $APP_DIR"
[ "$DO_OPEN" -eq 1 ] && open "$APP_DIR" || true
