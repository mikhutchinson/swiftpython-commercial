#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_VERSION="${1:-$(tr -d '[:space:]' < "$REPO_DIR/VERSION")}"
MANIFEST_PATH="${SWIFTPYTHON_RELEASE_MANIFEST:-}"

fail() {
    echo "release-surface audit failed: $*" >&2
    exit 1
}

require_file() {
    [ -f "$1" ] || fail "missing file: $1"
}

require_dir() {
    [ -d "$1" ] || fail "missing directory: $1"
}

require_file "$REPO_DIR/VERSION"
actual_version="$(tr -d '[:space:]' < "$REPO_DIR/VERSION")"
[ "$actual_version" = "$EXPECTED_VERSION" ] \
    || fail "VERSION is $actual_version, expected $EXPECTED_VERSION"

python3 - "$REPO_DIR" "$EXPECTED_VERSION" <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
readme = (root / "README.md").read_text()
license_text = (root / "LICENSE").read_text()
required = [
    f"Current release: `{version}`",
    "SwiftPythonRuntime.xcframework",
    "SwiftPythonAudioInterop.xcframework",
    "SwiftPythonMetalInterop.xcframework",
    "_swiftpython_wire.py",
    "_swiftpython_duplex.py",
    "swiftpython_protocol.py",
    "swiftpython_supervisor.py",
    "swiftpython_worker.py",
    "Worker wire v6",
    "DuplexSession",
    "three app-shaped",
    "20 consecutive positive warm restores",
    "SWIFTPYTHON_VM_RELEASE_GATE=1",
]
missing = [item for item in required if item not in readme]
if missing:
    raise SystemExit(f"README missing release facts: {missing}")
for item in (
    "SwiftPythonRuntime.xcframework",
    "SwiftPythonAudioInterop.xcframework",
    "SwiftPythonMetalInterop.xcframework",
    "licensing@swiftpython.dev",
):
    if item not in license_text:
        raise SystemExit(f"LICENSE missing current distribution fact: {item}")

forbidden = {
    "docs/api-guide": [
        "PyObjectRef.capsule",
        "extractCapsule",
        "The v0.5.14 commercial package",
        "Do not move `PyObjectRef` across `await`",
    ],
}
for relative, needles in forbidden.items():
    for path in (root / relative).rglob("*.md"):
        text = path.read_text()
        for needle in needles:
            if needle in text:
                raise SystemExit(f"stale API text {needle!r} in {path}")

guide = (root / "docs/api-guide/README.md").read_text()
for chapter in ("ch10-full-duplex.md", "ch11-apple-interop.md"):
    if chapter not in guide or not (root / "docs/api-guide" / chapter).is_file():
        raise SystemExit(f"API guide does not index {chapter}")

package = (root / "Package.swift").read_text()
for product in (
    "SwiftPythonRuntime",
    "SwiftPythonAudioInterop",
    "SwiftPythonMetalInterop",
):
    if package.count(f'name: "{product}"') < 2:
        raise SystemExit(f"Package.swift does not expose binary product {product}")

expected_helpers = {
    "_swiftpython_wire.py",
    "_swiftpython_duplex.py",
    "swiftpython_protocol.py",
    "swiftpython_supervisor.py",
    "swiftpython_worker.py",
}
actual_helpers = {
    path.name
    for path in (root / "VMWorker").glob("*.py")
}
if actual_helpers != expected_helpers:
    raise SystemExit(
        "VMWorker helper set mismatch: "
        f"missing={sorted(expected_helpers - actual_helpers)} "
        f"extra={sorted(actual_helpers - expected_helpers)}"
    )

release_placeholder_prefixes = tuple(
    "__" + product + "_XCFRAMEWORK_"
    for product in ("RUNTIME", "AUDIO", "METAL")
)

for path in root.rglob("*"):
    relative = path.relative_to(root)
    if path.name == ".env" or path.name.startswith(".env."):
        raise SystemExit(f"environment/secrets file in distribution: {relative}")
    if path.is_dir() and path.name.endswith(".dSYM"):
        raise SystemExit(f"private dSYM in distribution: {relative}")
    if not path.is_file():
        continue
    parts = set(relative.parts)
    if "__pycache__" in parts or path.suffix == ".pyc":
        raise SystemExit(f"generated Python bytecode in distribution: {relative}")
    if path.suffix in {".md", ".swift", ".sh", ".py", ".json", ".plist"}:
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        if any(prefix in text for prefix in release_placeholder_prefixes):
            raise SystemExit(f"unresolved release placeholder in {relative}")

for path in sorted(root.rglob("*.md")):
    if ".git" in path.parts or any(
        component.endswith(".xcframework") for component in path.parts
    ):
        continue
    text = path.read_text()
    if text.count("```") % 2:
        raise SystemExit(f"unbalanced Markdown code fences in {path}")
    prose = "\n".join(text.split("```")[::2])
    for target in re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", prose):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        relative_target = target.split("#", 1)[0]
        if relative_target and not (path.parent / relative_target).resolve().exists():
            raise SystemExit(f"broken relative Markdown link {target!r} in {path}")
PY

python3 - "$REPO_DIR" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
for path in sorted(root.rglob("*.py")):
    if ".git" in path.parts:
        continue
    compile(path.read_text(), str(path), "exec")
PY

for path in "$REPO_DIR"/Entitlements/*.plist "$REPO_DIR"/Entitlements/*.entitlements; do
    plutil -lint "$path" >/dev/null
done
for path in "$REPO_DIR"/scripts/*.sh "$REPO_DIR"/Examples/IrisDemo/scripts/*.sh; do
    bash -n "$path"
done

python3 - "$REPO_DIR/Entitlements/SwiftPythonWorker-sandbox.entitlements" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    payload = plistlib.load(handle)
expected = {
    "com.apple.security.app-sandbox": True,
    "com.apple.security.inherit": True,
}
if payload != expected:
    raise SystemExit(
        "inherited worker entitlements must contain exactly app-sandbox + inherit; "
        f"found {payload}"
    )
PY

for example in \
    BridgingRing \
    CoreRuntimeSmoke \
    DuplexSession \
    IrisDemo \
    ProcessPoolSmoke \
    SharedTensorPipeline
do
    dump="$(
        SWIFTPYTHON_COMMERCIAL_PACKAGE_URL=https://example.invalid/swiftpython-commercial.git \
        SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION="$EXPECTED_VERSION" \
        swift package --package-path "$REPO_DIR/Examples/$example" dump-package
    )"
    python3 - "$example" "$EXPECTED_VERSION" "$dump" <<'PY'
import json
import sys

name, version, raw = sys.argv[1:]
payload = json.loads(raw)
requirements = []
for dependency in payload.get("dependencies", []):
    for source in dependency.get("sourceControl", []):
        requirements.append(source.get("requirement", {}))
if not any(item.get("exact") == [version] for item in requirements):
    raise SystemExit(
        f"{name} did not preserve exact prerelease {version}: {requirements}"
    )
PY
done

root_package="$(swift package --package-path "$REPO_DIR" dump-package)"
python3 - "$root_package" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
products = {item["name"] for item in payload.get("products", [])}
expected = {
    "SwiftPythonRuntime",
    "SwiftPythonAudioInterop",
    "SwiftPythonMetalInterop",
    "swiftpython-smoke",
}
if products != expected:
    raise SystemExit(
        f"root Package.swift product mismatch: expected={sorted(expected)} "
        f"actual={sorted(products)}"
    )
PY

modules=(
    SwiftPythonRuntime
    SwiftPythonAudioInterop
    SwiftPythonMetalInterop
)
for module in "${modules[@]}"; do
    framework="$REPO_DIR/$module.xcframework"
    require_dir "$framework"
    info="$framework/Info.plist"
    require_file "$info"
    slice="$framework/macos-arm64_x86_64"
    require_dir "$slice"
    root_modules="$slice/$module.swiftmodule"
    header_modules="$slice/Headers/$module.swiftmodule"
    require_dir "$root_modules"
    require_dir "$header_modules"
    for arch in arm64 x86_64; do
        for suffix in swiftmodule swiftdoc swiftinterface private.swiftinterface; do
            root_file="$root_modules/$arch-apple-macos.$suffix"
            header_file="$header_modules/$arch-apple-macos.$suffix"
            require_file "$root_file"
            require_file "$header_file"
            cmp -s "$root_file" "$header_file" \
                || fail "$module $arch $suffix differs between module layouts"
        done
    done
    library="$slice/lib${module}-universal.a"
    if [ "$module" = SwiftPythonRuntime ]; then
        library="$slice/libRuntime-universal.a"
    fi
    require_file "$library"
    archs="$(lipo -archs "$library")"
    case " $archs " in
        *" arm64 "*) ;;
        *) fail "$module archive lacks arm64: $archs" ;;
    esac
    case " $archs " in
        *" x86_64 "*) ;;
        *) fail "$module archive lacks x86_64: $archs" ;;
    esac
done

require_file "$REPO_DIR/SwiftPythonWorker"

python3 - "$REPO_DIR" <<'PY'
import pathlib
import plistlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
worker = root / "SwiftPythonWorker"
completed = subprocess.run(
    ["codesign", "-d", "--entitlements", ":-", str(worker)],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    check=True,
)
observed = plistlib.loads(completed.stdout)
with (root / "Entitlements/SwiftPythonWorker.entitlements").open("rb") as handle:
    expected = plistlib.load(handle)
if observed != expected:
    raise SystemExit(
        "signed worker entitlements do not match the non-sandbox template: "
        f"observed={observed!r} expected={expected!r}"
    )
PY

codesign --verify --strict --verbose=2 "$REPO_DIR/SwiftPythonWorker"
signing_info="$(codesign -dv --verbose=4 "$REPO_DIR/SwiftPythonWorker" 2>&1)"
grep -q '^Identifier=com.swiftpython.worker$' <<<"$signing_info" \
    || fail "worker signing identifier is not com.swiftpython.worker"
grep -q '^Authority=Developer ID Application:' <<<"$signing_info" \
    || fail "worker is not signed by a Developer ID Application identity"
grep -q 'flags=.*runtime' <<<"$signing_info" \
    || fail "worker signature does not enable hardened runtime"
worker_archs="$(lipo -archs "$REPO_DIR/SwiftPythonWorker")"
[ "$worker_archs" = arm64 ] \
    || fail "shipped worker must be the documented arm64 sidecar, found $worker_archs"

python3 - "$REPO_DIR" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
required = {
    "SwiftPythonRuntime": [
        "PythonDuplexSession",
        "SandboxPool",
        "SandboxSnapshotValidationMode",
        "UbuntuImageBuilder",
    ],
    "SwiftPythonAudioInterop": [
        "DuplexAudioFormat",
        "DuplexAudioCapture",
        "DuplexAudioPlayback",
    ],
    "SwiftPythonMetalInterop": [
        "DuplexCopyLedger",
        "DuplexSharedMetalBufferLease",
        "DuplexMetalRegionPool",
    ],
}
for module, names in required.items():
    interface = (
        root
        / f"{module}.xcframework"
        / "macos-arm64_x86_64"
        / f"{module}.swiftmodule"
        / "arm64-apple-macos.swiftinterface"
    )
    text = interface.read_text()
    missing = [name for name in names if name not in text]
    if missing:
        raise SystemExit(f"{module} public interface missing {missing}")
    for leaked in ("/Users/", "CascadeProjects/", "/.build/"):
        if leaked in text:
            raise SystemExit(f"{module} public interface leaks build path {leaked!r}")
PY

if [ -n "$MANIFEST_PATH" ]; then
    require_file "$MANIFEST_PATH"
    python3 - "$REPO_DIR" "$MANIFEST_PATH" "$EXPECTED_VERSION" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
manifest_path = pathlib.Path(sys.argv[2])
version = sys.argv[3]
manifest = json.loads(manifest_path.read_text())
if manifest.get("version") != version:
    raise SystemExit(
        f"manifest version {manifest.get('version')!r} != {version!r}"
    )
if manifest.get("sourceTreeState") != "clean":
    raise SystemExit("release manifest sourceTreeState is not clean")
protocols = manifest.get("protocols", {})
if protocols.get("workerWire") != 6:
    raise SystemExit(f"manifest worker wire is not 6: {protocols}")
if "vmImage" not in manifest:
    raise SystemExit("VM release manifest lacks same-version image attestation")

artifacts = manifest.get("artifacts", [])
records = {(item.get("role"), item.get("name")): item for item in artifacts}
expected_records = {
    ("binaryTarget", "SwiftPythonRuntime.xcframework.zip"),
    ("binaryTarget", "SwiftPythonAudioInterop.xcframework.zip"),
    ("binaryTarget", "SwiftPythonMetalInterop.xcframework.zip"),
    ("workerExecutable", "SwiftPythonWorker"),
    ("completeDistribution", f"SwiftPythonCommercial-{version}.zip"),
    *(("vmGuestHelper", name) for name in (
        "_swiftpython_wire.py",
        "_swiftpython_duplex.py",
        "swiftpython_protocol.py",
        "swiftpython_supervisor.py",
        "swiftpython_worker.py",
    )),
}
if set(records) != expected_records:
    raise SystemExit(
        "manifest artifact inventory mismatch: "
        f"missing={sorted(expected_records - set(records))} "
        f"extra={sorted(set(records) - expected_records)}"
    )

for key, record in records.items():
    path = manifest_path.parent / record.get("path", "")
    if not path.is_file():
        raise SystemExit(f"manifest artifact does not exist for {key}: {path}")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != record.get("sha256"):
        raise SystemExit(f"manifest SHA-256 mismatch for {key}")
    if len(data) != record.get("bytes"):
        raise SystemExit(f"manifest byte count mismatch for {key}")
    if key[0] == "binaryTarget" and record.get("swiftPMChecksum") != digest:
        raise SystemExit(f"SwiftPM checksum mismatch for {key}")

for helper in (
    "_swiftpython_wire.py",
    "_swiftpython_duplex.py",
    "swiftpython_protocol.py",
    "swiftpython_supervisor.py",
    "swiftpython_worker.py",
):
    record = records.get(("vmGuestHelper", helper))
    if record is None:
        raise SystemExit(f"manifest lacks helper record for {helper}")
    data = (root / "VMWorker" / helper).read_bytes()
    if hashlib.sha256(data).hexdigest() != record.get("sha256"):
        raise SystemExit(f"manifest helper hash mismatch for {helper}")
PY
fi

echo "release-surface audit passed for $EXPECTED_VERSION"
