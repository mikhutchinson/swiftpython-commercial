#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO="${1:-}"
shift || true
case "$DEMO" in
    IrisDemo)
        APP_NAME=IRIS
        PRODUCT=IrisDemo
        APP_ID=dev.swiftpython.iris
        PACKAGES=(numpy scipy scikit-learn joblib threadpoolctl)
        ;;
    ParticleShowcase)
        APP_NAME="Particle Showcase"
        PRODUCT=particle-showcase
        APP_ID=dev.swiftpython.particle-showcase
        PACKAGES=(numpy)
        ;;
    *) echo "Usage: $0 IrisDemo|ParticleShowcase [--clean] [--open]" >&2; exit 64 ;;
esac
PKG_DIR="$REPO_DIR/Examples/$DEMO"
APP_DIR="$PKG_DIR/build/$APP_NAME.app"
APP_BINARY="$APP_DIR/Contents/MacOS/$PRODUCT"
OPEN_APP=0
for arg in "$@"; do
    case "$arg" in
        --open|--run) OPEN_APP=1 ;;
        --clean) rm -rf "$PKG_DIR/.build" "$PKG_DIR/build" ;;
        *) echo "Unknown argument: $arg" >&2; exit 64 ;;
    esac
done
export PYTHONDONTWRITEBYTECODE=1
export SWIFTPYTHON_AUTOBUILD_WORKER=0
ARCH="$(uname -m)"
case "$ARCH" in arm64|x86_64) ;; *) echo "Unsupported Mac architecture: $ARCH" >&2; exit 69 ;; esac
for tool in swift xcrun codesign ditto install_name_tool otool; do
    command -v "$tool" >/dev/null || { echo "Missing build tool: $tool" >&2; exit 69; }
done
BUILD_PYTHON="$(xcrun --find python3)"

swift build -c release --package-path "$PKG_DIR" --product "$PRODUCT"
BIN_DIR="$(swift build -c release --package-path "$PKG_DIR" --show-bin-path)"
RUNTIME_REPO="$REPO_DIR"
if [ -n "${SWIFTPYTHON_COMMERCIAL_PACKAGE_URL:-}" ]; then
    dependency_name="$(basename "${SWIFTPYTHON_COMMERCIAL_PACKAGE_URL%/}" .git)"
    RUNTIME_REPO="$PKG_DIR/.build/checkouts/$dependency_name"
    resolved_version="$(tr -d '[:space:]' < "$RUNTIME_REPO/VERSION")"
    [ "$resolved_version" = "${SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION:-}" ] \
        || { echo "Resolved worker does not match the requested runtime version" >&2; exit 1; }
fi
VERSION="$(tr -d '[:space:]' < "$RUNTIME_REPO/VERSION")"
STAGED_BINARY="$(mktemp "${TMPDIR:-/tmp}/swiftpython-demo-binary.XXXXXX")"
trap 'rm -f "$STAGED_BINARY"' EXIT
cp "$BIN_DIR/$PRODUCT" "$STAGED_BINARY"
chmod 0755 "$STAGED_BINARY"
if ! otool -l "$STAGED_BINARY" | grep -Fq '@executable_path/../Frameworks'; then
    install_name_tool -add_rpath '@executable_path/../Frameworks' "$STAGED_BINARY"
fi
codesign --force --sign - "$STAGED_BINARY"
codesign --verify --strict "$STAGED_BINARY"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/Frameworks" "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$STAGED_BINARY" "$APP_BINARY"
for framework in SwiftPythonEngine.framework Python.framework; do
    [ -d "$BIN_DIR/$framework" ] || { echo "SwiftPM did not stage $framework" >&2; exit 1; }
    ditto "$BIN_DIR/$framework" "$APP_DIR/Contents/Frameworks/$framework"
done
FOUND_RESOURCE_BUNDLE=0
while IFS= read -r -d '' RESOURCE_BUNDLE; do
    FOUND_RESOURCE_BUNDLE=1
    ditto "$RESOURCE_BUNDLE" "$APP_DIR/Contents/Resources/$(basename "$RESOURCE_BUNDLE")"
done < <(find "$BIN_DIR" -maxdepth 1 -type d \( -name '*.bundle' -o -name '*.resources' \) -print0)
[ "$FOUND_RESOURCE_BUNDLE" = 1 ] || { echo "Missing demo resource bundle" >&2; exit 1; }

cp "$RUNTIME_REPO/SwiftPythonWorker" "$APP_DIR/Contents/MacOS/SwiftPythonWorker"
"$BUILD_PYTHON" "$REPO_DIR/scripts/vendor_demo_python.py" \
    --lock "$REPO_DIR/scripts/python-wheels.lock.json" \
    --output "$APP_DIR/Contents/Resources/PythonPackages" \
    --cache "$PKG_DIR/build/wheel-cache" --arch "$ARCH" "${PACKAGES[@]}"

# Wheels contain native extensions and private libraries. Sign those before
# sealing the app; the release Python and Engine frameworks retain their seals.
while IFS= read -r -d '' native; do
    codesign --force --sign - "$native"
done < <(find "$APP_DIR/Contents/Resources/PythonPackages" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)

"$BUILD_PYTHON" - "$APP_DIR" "$PRODUCT" "$APP_NAME" "$APP_ID" "$VERSION" <<'PY'
import plistlib
import sys
from pathlib import Path
app, product, name, identifier, version = sys.argv[1:]
with (Path(app) / 'Contents/Info.plist').open('wb') as stream:
    plistlib.dump({
        'CFBundleExecutable': product,
        'CFBundleIdentifier': identifier,
        'CFBundleName': name,
        'CFBundleDisplayName': name,
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': version.split('-')[0],
        'CFBundleVersion': version.split('-duplex.')[-1],
        'SwiftPythonRuntimeVersion': version,
        'LSApplicationCategoryType': 'public.app-category.developer-tools',
        'LSMinimumSystemVersion': '15.0',
        'NSHighResolutionCapable': True,
        'NSPrincipalClass': 'NSApplication',
    }, stream)
PY
codesign --force --sign - "$APP_DIR"
codesign --verify --deep --strict "$APP_DIR"
codesign --verify --deep --strict "$APP_DIR/Contents/Frameworks/Python.framework"
printf 'Created: %s\n' "$APP_DIR"
if [ "$OPEN_APP" = 1 ]; then open "$APP_DIR"; fi
