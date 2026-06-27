#!/usr/bin/env bash
# Bundle a self-contained Python 3.13 + the scientific stack into Studio.app so it
# runs on a Mac with NO system Python. Invoked by build_app.sh --bundle-python.
#
# Lean payload (per the launch decision): numpy, pandas, scikit-learn, scipy.
#
# Strategy: copy the Homebrew Python 3.13 framework as a relocatable interpreter,
# then `pip install --target` the wheels into a bundled site-packages. The app
# points the worker at this interpreter via PYTHONHOME/PYTHONPATH (see
# SHIPPING.md "Wiring the worker to the bundled interpreter").
#
# NOTE: This is the bundling scaffold. The exact PYTHONHOME wiring + the
# inside-out signing of every wheel .so are covered in SHIPPING.md and must be
# verified end-to-end on the release machine (Developer ID + notarytool).
set -euo pipefail

APP_DIR="${1:?usage: bundle_python.sh <App.app>}"
PYVER="3.13"
SRC_FRAMEWORK="${STUDIO_PYTHON_FRAMEWORK:-/opt/homebrew/opt/python@${PYVER}/Frameworks/Python.framework}"
WHEELS=("numpy" "pandas" "scikit-learn" "scipy")

FRAMEWORKS_DIR="$APP_DIR/Contents/Frameworks"
PY_RES_DIR="$APP_DIR/Contents/Resources/Python"
SITE_PKGS="$PY_RES_DIR/site-packages"

[ -d "$SRC_FRAMEWORK" ] || { echo "Python.framework not found at $SRC_FRAMEWORK" >&2; exit 1; }

echo "==> copying Python.framework"
mkdir -p "$FRAMEWORKS_DIR"
ditto "$SRC_FRAMEWORK" "$FRAMEWORKS_DIR/Python.framework"

echo "==> installing wheels into bundled site-packages: ${WHEELS[*]}"
mkdir -p "$SITE_PKGS"
PYBIN="$FRAMEWORKS_DIR/Python.framework/Versions/$PYVER/bin/python$PYVER"
"$PYBIN" -m pip install --upgrade --no-cache-dir --target "$SITE_PKGS" "${WHEELS[@]}"

echo "==> removing symlinks that escape the framework (Gatekeeper rejects them)"
# e.g. Homebrew's framework links lib/python3.13/site-packages up into the Cellar.
# We use Contents/Resources/Python/site-packages via PYTHONPATH instead.
FW="$FRAMEWORKS_DIR/Python.framework"
find "$FW" -type l | while IFS= read -r link; do
    target="$(cd "$(dirname "$link")" && readlink "$link")"
    resolved="$(cd "$(dirname "$link")" 2>/dev/null && cd "$(dirname "$target")" 2>/dev/null && pwd)" || resolved=""
    # Delete if the symlink target resolves outside the framework, or is absolute.
    case "$target" in
        /*) rm -f "$link"; echo "    removed absolute symlink: ${link#$APP_DIR/}" ;;
        *)  if [ -n "$resolved" ] && [[ "$resolved" != "$FW"* ]]; then
                rm -f "$link"; echo "    removed escaping symlink: ${link#$APP_DIR/}"
            fi ;;
    esac
done

echo "==> normalizing executable bits on non-Mach-O Python payloads (Gatekeeper)"
find "$FRAMEWORKS_DIR/Python.framework" "$PY_RES_DIR" -type f -perm -111 2>/dev/null | while IFS= read -r f; do
    file "$f" | grep -q 'Mach-O' || chmod -x "$f"
done

echo "==> bundled Python ready under Contents/Frameworks + Contents/Resources/Python"
echo "    (build_app.sh --sign will Developer-ID-sign every wheel .so inside-out)"
