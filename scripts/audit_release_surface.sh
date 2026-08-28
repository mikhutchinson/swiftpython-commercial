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
    "SwiftPythonEngine.xcframework",
    "SwiftPythonAudioInterop.xcframework",
    "SwiftPythonMetalInterop.xcframework",
    "SwiftPythonAudioProbe",
    "_swiftpython_wire.py",
    "_swiftpython_duplex.py",
    "swiftpython_protocol.py",
    "swiftpython_supervisor.py",
    "swiftpython_worker.py",
    "Worker wire v6",
    "DuplexSession",
    "three app-shaped",
    "20 consecutive positive warm restores",
    "NSMicrophoneUsageDescription",
    "SWIFTPYTHON_AUDIO_PROBE_GATE=ready",
    "SWIFTPYTHON_VM_RELEASE_GATE=1",
    'SWIFTPYTHON_NOTARY_PROFILE="<notarytool-keychain-profile>"',
]
missing = [item for item in required if item not in readme]
if missing:
    raise SystemExit(f"README missing release facts: {missing}")
for item in (
    "SwiftPythonRuntime.xcframework",
    "SwiftPythonEngine.xcframework",
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

process_pool_guide = (root / "docs/api-guide/ch4-process-pool.md").read_text()
for public_name in (
    "shedIdleWorkersAndWait(force:)",
    "drainTimedOut(inFlight:seconds:)",
    "hostAsyncCallbackQuiescenceTimedOut",
    "workerLifecycleQuiescenceTimedOut",
    "workerTransportCleanupTimedOut",
    "workerTransportCleanupFailed",
):
    if public_name not in process_pool_guide:
        raise SystemExit(
            f"ProcessPool API guide missing current public name: {public_name}"
        )

duplex_guide = (root / "docs/api-guide/ch10-full-duplex.md").read_text()
for public_name in (
    "DuplexFailure",
    "DuplexFailureCode",
    "DuplexFailureOrigin",
    "Hashable",
):
    if public_name not in duplex_guide:
        raise SystemExit(
            f"duplex API guide missing current public shape: {public_name}"
        )

audio_guide = (root / "docs/api-guide/ch11-apple-interop.md").read_text()
for public_name in (
    "DuplexAudioCaptureNegotiation",
    "DuplexAudioPlaybackNegotiation",
    "startRun(sendingTo:)",
    "stop(mode:)",
    "runReporting(output:)",
    "DuplexAudioCaptureError",
    "DuplexAudioPlaybackError",
    "DuplexAudioRestartRequirement",
):
    if public_name not in audio_guide:
        raise SystemExit(
            f"Audio API guide missing current public name: {public_name}"
        )

package = (root / "Package.swift").read_text()
for product in (
    "SwiftPythonRuntime",
    "SwiftPythonAudioInterop",
    "SwiftPythonMetalInterop",
):
    if package.count(f'name: "{product}"') < 2:
        raise SystemExit(f"Package.swift does not expose binary product {product}")
if package.count('.binaryTarget(\n            name: "SwiftPythonEngine"') != 1:
    raise SystemExit("Package.swift must declare exactly one private Engine binary target")
if '.library(name: "SwiftPythonEngine"' in package:
    raise SystemExit("SwiftPythonEngine must not be exposed as a product")

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
    for product in ("RUNTIME", "ENGINE", "AUDIO", "METAL")
)

for path in root.rglob("*"):
    relative = path.relative_to(root)
    if path.is_dir() and path.name in {
        ".build",
        ".swiftpm",
        ".plan",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        "Artifacts",
        "DerivedData",
        "__pycache__",
        "build",
    }:
        raise SystemExit(f"forbidden generated directory in distribution: {relative}")
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

PYTHONDONTWRITEBYTECODE=1 \
    python3 "$REPO_DIR/scripts/test_audio_probe_release_contract.py"

for path in "$REPO_DIR"/Entitlements/*.plist "$REPO_DIR"/Entitlements/*.entitlements; do
    plutil -lint "$path" >/dev/null
done

# This contract audits both raw Python-linked executables. Each must carry the
# canonical @rpath Python load command and embedded-framework LC_RPATH; any
# absolute Python.framework load fails before the remaining release checks.
python_runtime_audit=(
    python3
    "$REPO_DIR/scripts/audio_probe_release_contract.py"
    --repo "$REPO_DIR"
    --expected-version "$EXPECTED_VERSION"
)
if [ -n "$MANIFEST_PATH" ]; then
    python_runtime_audit+=(--manifest "$MANIFEST_PATH")
fi
"${python_runtime_audit[@]}"

engine_xcframework="$REPO_DIR/SwiftPythonEngine.xcframework"
require_dir "$engine_xcframework"
require_file "$engine_xcframework/Info.plist"
engine_slice="$engine_xcframework/macos-arm64_x86_64"
require_dir "$engine_slice"
engine_framework="$engine_slice/SwiftPythonEngine.framework"
require_dir "$engine_framework"
engine_binary="$engine_framework/Versions/A/SwiftPythonEngine"
require_file "$engine_binary"
require_file "$engine_framework/Versions/A/Headers/SwiftPythonEngine.h"
require_file "$engine_framework/Versions/A/Modules/module.modulemap"
if find "$engine_xcframework" -type f \( \
    -name '*.swiftinterface' -o \
    -name '*.swiftmodule' -o \
    -name '*.swiftdoc' -o \
    -name '*.swiftsourceinfo' -o \
    -name '*.swift' -o \
    -name '*.abi.json' \
\) -print -quit | grep -q .; then
    fail "private Engine contains a Swift module, interface, docs, ABI JSON, or source"
fi
engine_archs="$(lipo -archs "$engine_binary")"
for required_arch in arm64 x86_64; do
    case " $engine_archs " in
        *" $required_arch "*) ;;
        *) fail "private Engine lacks $required_arch: $engine_archs" ;;
    esac
done
engine_id="$(otool -D "$engine_binary")"
grep -q '@rpath/SwiftPythonEngine.framework/Versions/A/SwiftPythonEngine' \
    <<<"$engine_id" \
    || fail "private Engine install name is not relocatable"
if otool -L "$engine_binary" | grep -E 'Python\.framework|libpython' >/dev/null; then
    fail "private Engine contains a build-machine Python load command"
fi
codesign --verify --deep --strict --verbose=2 "$engine_framework"
engine_signing_info="$(codesign -dv --verbose=4 "$engine_framework" 2>&1)"
grep -q '^Authority=Developer ID Application:' <<<"$engine_signing_info" \
    || fail "private Engine is not Developer ID signed"
grep -q 'flags=.*runtime' <<<"$engine_signing_info" \
    || fail "private Engine signature does not enable hardened runtime"
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

# Type-check every command-line example directly against the shipped public
# module. Package-manifest validation alone cannot catch an example reaching a
# package-only API. IrisDemo is built by the post-publication example gate
# because SwiftPM must synthesize its resource-bundle accessor.
for example in \
    BridgingRing \
    CoreRuntimeSmoke \
    DuplexSession \
    ProcessPoolSmoke \
    SharedTensorPipeline
do
    example_sources=()
    while IFS= read -r source; do
        example_sources+=("$source")
    done < <(
        find "$REPO_DIR/Examples/$example/Sources" \
            -name '*.swift' \
            -print \
            | sort
    )
    [ "${#example_sources[@]}" -gt 0 ] \
        || fail "$example has no Swift source files"
    xcrun swiftc \
        -typecheck \
        -parse-as-library \
        -target arm64-apple-macos15.0 \
        -I "$REPO_DIR/SwiftPythonRuntime.xcframework/macos-arm64_x86_64/Headers" \
        "${example_sources[@]}"
done

root_package="$(swift package --package-path "$REPO_DIR" dump-package)"
python3 - "$root_package" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
products = {item["name"] for item in payload.get("products", [])}
public_products = {
    "SwiftPythonRuntime",
    "SwiftPythonAudioInterop",
    "SwiftPythonMetalInterop",
}
allowed = (public_products, public_products | {"swiftpython-smoke"})
if products not in allowed:
    raise SystemExit(
        "root Package.swift product mismatch: expected either "
        f"{sorted(public_products)} or "
        f"{sorted(public_products | {'swiftpython-smoke'})}; "
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

# These shipped source files are compile-only documentation consumers. Keep
# them outside the URL package graph so the package retains exactly four
# binary targets, but type-check them against the candidate interfaces here.
public_module_flags=()
for module in "${modules[@]}"; do
    public_module_flags+=(
        -I
        "$REPO_DIR/$module.xcframework/macos-arm64_x86_64/Headers"
    )
done
require_file "$REPO_DIR/Sources/SwiftPythonSmoke/main.swift"
xcrun swiftc \
    -typecheck \
    -parse-as-library \
    -target arm64-apple-macos15.0 \
    "${public_module_flags[@]}" \
    "$REPO_DIR/Sources/SwiftPythonSmoke/main.swift"

documentation_tests=(
    "$REPO_DIR/Tests/SwiftPythonSmokeTests/PublicDocumentationAPITests.swift"
    "$REPO_DIR/Tests/SwiftPythonSmokeTests/SwiftPythonSmokeTests.swift"
)
for documentation_test in "${documentation_tests[@]}"; do
    require_file "$documentation_test"
done
developer_dir="$(xcode-select -p)"
xctest_platform="$developer_dir/Platforms/MacOSX.platform/Developer"
require_dir "$xctest_platform/usr/lib/XCTest.swiftmodule"
require_dir "$xctest_platform/Library/Frameworks/XCTest.framework"
xcrun swiftc \
    -typecheck \
    -parse-as-library \
    -target arm64-apple-macos15.0 \
    -I "$xctest_platform/usr/lib" \
    -F "$xctest_platform/Library/Frameworks" \
    "${public_module_flags[@]}" \
    "${documentation_tests[@]}"

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
        "SandboxProvider",
        "SandboxConfiguration",
        "ManagedBuffer",
    ],
    "SwiftPythonAudioInterop": [
        "DuplexAudioFormat",
        "DuplexAudioCapture",
        "DuplexAudioPlayback",
        "DuplexAudioHardwareProbeConfiguration",
        "DuplexAudioHardwareProbeOutcome",
        "DuplexAudioHardwareProbeReport",
        "DuplexAudioHardwareProbeLauncher",
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

runtime_root = root / "SwiftPythonRuntime.xcframework" / "macos-arm64_x86_64"
denied = (
    "SwiftPythonEngine",
    "WorkerCommand",
    "WorkerResponse",
    "DuplexWire",
    "SharedMemoryArena",
    "DuplexShared",
    "workerGeneration",
    "quarantinedSlots",
    "VMManager",
    "VZVirtualMachine",
    "UbuntuImageBuilder",
    "KernelBoot",
    "MLXPressurePolicy",
    "softPressureRatio",
    "@_spi(",
)
for layout in ("SwiftPythonRuntime.swiftmodule", "Headers/SwiftPythonRuntime.swiftmodule"):
    for arch in ("arm64", "x86_64"):
        for suffix in ("swiftinterface", "private.swiftinterface"):
            path = runtime_root / layout / f"{arch}-apple-macos.{suffix}"
            text = path.read_text()
            leaked = [token for token in denied if token in text]
            if leaked:
                raise SystemExit(f"Runtime interface leaks {leaked} in {path}")
PY

if [ -n "$MANIFEST_PATH" ]; then
    echo "manifest-backed release-surface audit passed for $EXPECTED_VERSION"
else
    echo "pre-manifest release-surface audit passed for $EXPECTED_VERSION (NOT manifest or release evidence)"
fi
