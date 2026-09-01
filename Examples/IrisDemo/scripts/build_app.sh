#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
APP_NAME="IRIS"
APP_DIR="$PKG_DIR/build/${APP_NAME}.app"
APP_BINARY="$APP_DIR/Contents/MacOS/$APP_NAME"
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

for required_tool in codesign ditto install_name_tool otool swift; do
    command -v "$required_tool" >/dev/null || {
        echo "Required tool not found: $required_tool" >&2
        exit 69
    }
done

if [ "$CLEAN" -eq 1 ]; then
    rm -rf "$APP_DIR"
fi

swift build --product IrisDemo
BIN_DIR="$(swift build --show-bin-path)"
STAGED_BINARY="$(mktemp "${TMPDIR:-/tmp}/swiftpython-iris-binary.XXXXXX")"
trap 'rm -f "$STAGED_BINARY"' EXIT
cp "$BIN_DIR/IrisDemo" "$STAGED_BINARY"
chmod 0755 "$STAGED_BINARY"

if ! otool -l "$STAGED_BINARY" \
    | grep -Fq '@executable_path/../Frameworks'; then
    install_name_tool \
        -add_rpath '@executable_path/../Frameworks' \
        "$STAGED_BINARY"
fi

# Sign the modified executable before it enters the .app. Signing it in place
# makes codesign inspect SwiftPM's data-only resource bundle as nested code.
codesign --force --sign - "$STAGED_BINARY"
codesign --verify --strict "$STAGED_BINARY"

rm -rf "$APP_DIR"
mkdir -p \
    "$APP_DIR/Contents/Frameworks" \
    "$APP_DIR/Contents/MacOS" \
    "$APP_DIR/Contents/Resources"

cp "$STAGED_BINARY" "$APP_BINARY"

for framework in SwiftPythonEngine.framework Python.framework; do
    if [ ! -d "$BIN_DIR/$framework" ]; then
        echo "SwiftPM did not stage required private framework: $BIN_DIR/$framework" >&2
        exit 1
    fi
    ditto \
        "$BIN_DIR/$framework" \
        "$APP_DIR/Contents/Frameworks/$framework"
done

FOUND_RESOURCE_BUNDLE=0
while IFS= read -r -d '' RESOURCE_BUNDLE; do
    FOUND_RESOURCE_BUNDLE=1
    cp -R "$RESOURCE_BUNDLE" "$APP_DIR/Contents/Resources/"
    cp -R "$RESOURCE_BUNDLE" "$APP_DIR/Contents/MacOS/"
done < <(find "$BIN_DIR" -maxdepth 1 -type d \( -name '*.resources' -o -name '*.bundle' \) -print0)

if [ "$FOUND_RESOURCE_BUNDLE" -eq 0 ]; then
    echo "SwiftPM resource bundle not found in $BIN_DIR" >&2
    exit 1
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

# The copied release frameworks retain their existing valid signatures.
# Distribution builds still use the inside-out release signing runbook.
codesign --verify --deep --strict \
    "$APP_DIR/Contents/Frameworks/SwiftPythonEngine.framework"
codesign --verify --deep --strict \
    "$APP_DIR/Contents/Frameworks/Python.framework"

echo "Created: $APP_DIR"

if [ "$OPEN_APP" -eq 1 ]; then
    open "$APP_DIR"
fi
