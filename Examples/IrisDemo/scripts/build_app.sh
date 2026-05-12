#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="$(cd "$PKG_DIR/../.." && pwd)"
APP_NAME="IRIS"
APP_DIR="$PKG_DIR/build/${APP_NAME}.app"
OPEN_APP=0
CLEAN=0

for arg in "$@"; do
    case "$arg" in
        --open|--run)
            OPEN_APP=1
            ;;
        --clean)
            CLEAN=1
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            echo "Usage: $0 [--clean] [--open]" >&2
            exit 64
            ;;
    esac
done

cd "$PKG_DIR"

if [ "$CLEAN" -eq 1 ]; then
    rm -rf "$APP_DIR"
fi

swift build --product IrisDemo
BIN_DIR="$(swift build --show-bin-path)"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cp "$BIN_DIR/IrisDemo" "$APP_DIR/Contents/MacOS/$APP_NAME"

RESOURCE_BUNDLE="$(find "$BIN_DIR" -maxdepth 1 -type d -name '*.resources' | head -1 || true)"
if [ -n "$RESOURCE_BUNDLE" ]; then
    cp -R "$RESOURCE_BUNDLE" "$APP_DIR/Contents/Resources/"
    cp -R "$RESOURCE_BUNDLE" "$APP_DIR/Contents/MacOS/"
fi

if [ -f "$REPO_DIR/SwiftPythonWorker" ]; then
    cp "$REPO_DIR/SwiftPythonWorker" "$APP_DIR/Contents/MacOS/SwiftPythonWorker"
fi

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>IRIS</string>
    <key>CFBundleIdentifier</key>
    <string>dev.swiftpython.iris</string>
    <key>CFBundleName</key>
    <string>IRIS</string>
    <key>CFBundleDisplayName</key>
    <string>IRIS</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.developer-tools</string>
    <key>LSMinimumSystemVersion</key>
    <string>15.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
PLIST

echo "Created: $APP_DIR"

if [ "$OPEN_APP" -eq 1 ]; then
    open "$APP_DIR"
fi
