#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/swiftpython-consumer-path-smoke.XXXXXX")"
LOCAL_PACKAGE_DIR="$WORK_DIR/swiftpython-commercial-local"
PYTHON_LIB_DIR="${SWIFTPYTHON_PYTHON_LIB_DIR:-}"
NOTARY_PROFILE="${SWIFTPYTHON_NOTARY_PROFILE:-}"
NOTARY_OUTPUT_DIR="${SWIFTPYTHON_NOTARY_OUTPUT_DIR:-}"
KEEP_WORK_DIR="${SWIFTPYTHON_KEEP_WORK_DIR:-0}"
VM_RELEASE_GATE="${SWIFTPYTHON_VM_RELEASE_GATE:-0}"
VM_BASE_IMAGE="${SWIFTPYTHON_VM_BASE_IMAGE:-}"
VM_SNAPSHOT="${SWIFTPYTHON_VM_SNAPSHOT:-}"
VM_RESTORE_SECRET="${SWIFTPYTHON_VM_RESTORE_SECRET:-}"
VM_CLONE_DIR="${SWIFTPYTHON_VM_CLONE_DIR:-}"
VM_ITERATIONS="${SWIFTPYTHON_VM_ITERATIONS:-20}"
AUDIO_PROBE_GATE="${SWIFTPYTHON_AUDIO_PROBE_GATE:-off}"
RELEASE_MANIFEST="${SWIFTPYTHON_RELEASE_MANIFEST:-}"
RELEASE_VERSION="$(tr -d '[:space:]' < "$REPO_DIR/VERSION")"
export SWIFTPYTHON_RELEASE_VERSION="$RELEASE_VERSION"
SMOKE_ID_SUFFIX="$(
    basename "$WORK_DIR" \
        | sed 's/.*\.//' \
        | tr -cd '[:alnum:]' \
        | tr '[:upper:]' '[:lower:]'
)"
DEVELOPER_BUNDLE_IDENTIFIER="ai.bestbyte.sp.d.$SMOKE_ID_SUFFIX"
SANDBOX_BUNDLE_IDENTIFIER="ai.bestbyte.sp.s.$SMOKE_ID_SUFFIX"

case "$AUDIO_PROBE_GATE" in
    off|containment|ready) ;;
    *)
        echo "SWIFTPYTHON_AUDIO_PROBE_GATE must be off, containment, or ready." >&2
        exit 64
        ;;
esac

for required_tool in \
    codesign \
    ditto \
    install_name_tool \
    lipo \
    otool \
    python3 \
    security \
    swift \
    xcodebuild
do
    command -v "$required_tool" >/dev/null \
        || { echo "Required tool not found: $required_tool" >&2; exit 69; }
done

cleanup() {
    if [ "$KEEP_WORK_DIR" = 1 ]; then
        echo "Consumer smoke work directory retained at $WORK_DIR"
    else
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

# A signed/notarized consumer run is not release evidence unless its exact
# schema-3 manifest ties every input ZIP, raw executable, five guest helpers,
# VM image attestation, and the complete distribution back to this checkout.
if [ -z "$RELEASE_MANIFEST" ]; then
    echo "Consumer smoke requires SWIFTPYTHON_RELEASE_MANIFEST." >&2
    exit 64
fi
PYTHONDONTWRITEBYTECODE=1 python3 \
    "$REPO_DIR/scripts/audio_probe_release_contract.py" \
    --repo "$REPO_DIR" \
    --expected-version "$RELEASE_VERSION" \
    --manifest "$RELEASE_MANIFEST"

CODESIGN_TIMESTAMP_ARGS=(--timestamp=none)
if [ -n "$NOTARY_PROFILE" ]; then
    if [ "$AUDIO_PROBE_GATE" != ready ]; then
        echo "Notary mode requires SWIFTPYTHON_AUDIO_PROBE_GATE=ready; containment/notReady evidence is not device readiness." >&2
        exit 64
    fi
    if [ -z "$NOTARY_OUTPUT_DIR" ]; then
        echo "Notary mode requires SWIFTPYTHON_NOTARY_OUTPUT_DIR so evidence is retained." >&2
        exit 64
    fi
    for required_tool in ditto jq lsappinfo open spctl xattr xcrun; do
        command -v "$required_tool" >/dev/null \
            || { echo "Required notary tool not found: $required_tool" >&2; exit 69; }
    done
    CODESIGN_TIMESTAMP_ARGS=(--timestamp)
    if ! xcrun notarytool history \
        --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
        echo "Notary profile could not be validated: $NOTARY_PROFILE" >&2
        exit 69
    fi
fi

if [ "$VM_RELEASE_GATE" = 1 ]; then
    if [ -z "$NOTARY_PROFILE" ]; then
        echo "VM release gate requires notarization mode." >&2
        exit 64
    fi
    for required_path in \
        "$VM_BASE_IMAGE" \
        "$VM_SNAPSHOT" \
        "$VM_RESTORE_SECRET"; do
        if [ ! -e "$required_path" ]; then
            echo "VM release-gate input does not exist: $required_path" >&2
            exit 66
        fi
    done
    if [ -z "$VM_CLONE_DIR" ]; then
        echo "VM release gate requires SWIFTPYTHON_VM_CLONE_DIR." >&2
        exit 64
    fi
    case "$VM_ITERATIONS" in
        ''|*[!0-9]*|0)
            echo "SWIFTPYTHON_VM_ITERATIONS must be a positive integer." >&2
            exit 64
            ;;
    esac
fi

# Keep release-gate paths out of ordinary unsigned/signed consumer launches.
# They are reintroduced only for the final notarized virtualization app.
unset \
    SWIFTPYTHON_VM_BASE_IMAGE \
    SWIFTPYTHON_VM_SNAPSHOT \
    SWIFTPYTHON_VM_RESTORE_SECRET \
    SWIFTPYTHON_VM_CLONE_DIR \
    SWIFTPYTHON_VM_ITERATIONS

if [ -z "$PYTHON_LIB_DIR" ]; then
    for candidate in \
        "/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib" \
        "/usr/local/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib" \
        "/opt/homebrew/opt/python@3.13/lib" \
        "/usr/local/opt/python@3.13/lib"; do
        if [ -d "$candidate" ]; then
            PYTHON_LIB_DIR="$candidate"
            break
        fi
    done
fi

if [ -z "$PYTHON_LIB_DIR" ]; then
    echo "Python 3.13 library directory not found; set SWIFTPYTHON_PYTHON_LIB_DIR." >&2
    exit 1
fi
PYTHON_HOME_DIR="$(cd "$PYTHON_LIB_DIR/.." && pwd)"
PYTHON_FRAMEWORK_DIR="$(cd "$PYTHON_HOME_DIR/../.." && pwd)"
PYTHON_FRAMEWORK_BINARY="$PYTHON_HOME_DIR/Python"
if [ ! -f "$PYTHON_FRAMEWORK_BINARY" ]; then
    echo "Python framework binary not found at $PYTHON_FRAMEWORK_BINARY." >&2
    exit 1
fi
PYTHON_FRAMEWORK_LOAD_PATH="$(
    otool -L "$REPO_DIR/SwiftPythonWorker" \
        | awk '/Python\.framework\/Versions\/3\.13\/Python/ { print $1; exit }'
)"
if [ -z "$PYTHON_FRAMEWORK_LOAD_PATH" ]; then
    echo "SwiftPythonWorker does not declare a Python 3.13 framework dependency." >&2
    exit 1
fi
AUDIO_PROBE="$REPO_DIR/SwiftPythonAudioProbe"
if [ ! -x "$AUDIO_PROBE" ] || [ -L "$AUDIO_PROBE" ] || [ ! -f "$AUDIO_PROBE" ]; then
    echo "Exact regular executable SwiftPythonAudioProbe is missing from the candidate." >&2
    exit 1
fi
AUDIO_PROBE_PYTHON_LOAD_PATH=""
for probe_architecture in arm64 x86_64; do
    architecture_python_loads="$(
        otool -arch "$probe_architecture" -L "$AUDIO_PROBE" \
            | awk '/Python\.framework\/Versions\/3\.13\/Python/ { print $1 }'
    )"
    if [ -z "$architecture_python_loads" ] \
            || [ "$(printf '%s\n' "$architecture_python_loads" | wc -l | tr -d '[:space:]')" != 1 ]; then
        echo "SwiftPythonAudioProbe $probe_architecture must declare exactly one Python 3.13 framework dependency." >&2
        exit 1
    fi
    if [ -z "$AUDIO_PROBE_PYTHON_LOAD_PATH" ]; then
        AUDIO_PROBE_PYTHON_LOAD_PATH="$architecture_python_loads"
    elif [ "$architecture_python_loads" != "$AUDIO_PROBE_PYTHON_LOAD_PATH" ]; then
        echo "SwiftPythonAudioProbe slices do not declare the same Python framework dependency." >&2
        exit 1
    fi
done
if [ "$AUDIO_PROBE_PYTHON_LOAD_PATH" != "$PYTHON_FRAMEWORK_LOAD_PATH" ]; then
    echo "Worker and audio probe do not load the same staged Python framework." >&2
    exit 1
fi
ENGINE_FRAMEWORK="$REPO_DIR/SwiftPythonEngine.xcframework/macos-arm64_x86_64/SwiftPythonEngine.framework"
ENGINE_BINARY="$ENGINE_FRAMEWORK/Versions/A/SwiftPythonEngine"
if [ ! -f "$ENGINE_BINARY" ]; then
    echo "SwiftPythonEngine universal framework binary not found at $ENGINE_BINARY." >&2
    exit 1
fi

run_with_timeout() {
    local timeout_seconds="$1"
    shift
    "$@" &
    local process_pid=$!
    (
        sleep "$timeout_seconds"
        if kill -0 "$process_pid" 2>/dev/null; then
            echo "Timed out after ${timeout_seconds}s: $*" >&2
            for child_pid in $(pgrep -P "$process_pid" 2>/dev/null || true); do
                kill -TERM "$child_pid" 2>/dev/null || true
            done
            kill -TERM "$process_pid" 2>/dev/null || true
            sleep 5
            for child_pid in $(pgrep -P "$process_pid" 2>/dev/null || true); do
                kill -KILL "$child_pid" 2>/dev/null || true
            done
            kill -KILL "$process_pid" 2>/dev/null || true
        fi
    ) &
    local watchdog_pid=$!
    local exit_code=0
    wait "$process_pid" || exit_code=$?
    kill "$watchdog_pid" 2>/dev/null || true
    wait "$watchdog_pid" 2>/dev/null || true
    return "$exit_code"
}

mkdir -p "$WORK_DIR/Sources/ConsumerSmoke"

write_local_binary_package() {
    mkdir -p "$LOCAL_PACKAGE_DIR"
    python3 - "$REPO_DIR/Package.swift" "$LOCAL_PACKAGE_DIR/Package.swift" <<'PY'
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
expected = {
    "SwiftPythonRuntime",
    "SwiftPythonEngine",
    "SwiftPythonAudioInterop",
    "SwiftPythonMetalInterop",
}
pattern = re.compile(
    r'\.binaryTarget\(\s*'
    r'name: "([^"]+)",\s*'
    r'url: "[^"]+",\s*'
    r'checksum: "[0-9a-f]{64}"\s*'
    r'\)',
    re.DOTALL,
)
found = set()

def replace(match: re.Match[str]) -> str:
    name = match.group(1)
    found.add(name)
    return (
        '.binaryTarget(\n'
        f'            name: "{name}",\n'
        f'            path: "{name}.xcframework"\n'
        '        )'
    )

rendered = pattern.sub(replace, source)
if found != expected:
    raise SystemExit(
        f"candidate package binary targets changed: expected {sorted(expected)}, "
        f"found {sorted(found)}"
    )
if "releases/download/" in rendered or "checksum:" in rendered:
    raise SystemExit("local candidate manifest still contains a remote binary target")
pathlib.Path(sys.argv[2]).write_text(rendered, encoding="utf-8")
PY

    for module in \
        SwiftPythonRuntime \
        SwiftPythonEngine \
        SwiftPythonAudioInterop \
        SwiftPythonMetalInterop
    do
        ditto \
            "$REPO_DIR/$module.xcframework" \
            "$LOCAL_PACKAGE_DIR/$module.xcframework"
    done
    swift package --package-path "$LOCAL_PACKAGE_DIR" dump-package >/dev/null
}

write_consumer_package() {
    local destination="$1"
    mkdir -p "$destination/Sources/ConsumerSmoke"
    cat > "$destination/Package.swift" <<EOF
// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ConsumerSmoke",
    platforms: [.macOS(.v15)],
    dependencies: [
        .package(name: "swiftpython-commercial", path: "$LOCAL_PACKAGE_DIR"),
    ],
    targets: [
        .executableTarget(
            name: "ConsumerSmoke",
            dependencies: [
                .product(name: "SwiftPythonRuntime", package: "swiftpython-commercial"),
                .product(name: "SwiftPythonAudioInterop", package: "swiftpython-commercial"),
                .product(name: "SwiftPythonMetalInterop", package: "swiftpython-commercial"),
            ],
            linkerSettings: [
                .unsafeFlags([
                    "-L$PYTHON_LIB_DIR",
                    "-lpython3.13",
                ])
            ]
        ),
    ]
)
EOF

    # Keep an @main source out of `main.swift`: SwiftPM accepts either shape,
    # but Xcode's package scheme otherwise treats the filename as a top-level
    # entry point and rejects the @main declaration.
    cat > "$destination/Sources/ConsumerSmoke/ConsumerSmoke.swift" <<'EOF'
import Foundation
import CryptoKit
import Darwin
@preconcurrency import AVFoundation
@preconcurrency import Metal
import SwiftPythonAudioInterop
import SwiftPythonMetalInterop
import SwiftPythonRuntime

@main
enum ConsumerSmoke {
    static func main() async {
        do {
            try configureBundledPythonHomeIfRequested()
            try await run()
            try publishLaunchServicesReceiptIfRequested()
        } catch {
            let diagnostic = Data("ConsumerSmoke failed: \(error)\n".utf8)
            try? FileHandle.standardError.write(contentsOf: diagnostic)
            Foundation.exit(EXIT_FAILURE)
        }
    }

    private static func configureBundledPythonHomeIfRequested() throws {
        guard ProcessInfo.processInfo.environment[
            "SWIFTPYTHON_USE_BUNDLED_PYTHON_HOME"
        ] == "1" else {
            return
        }
        guard let frameworks = Bundle.main.privateFrameworksURL else {
            throw ConsumerFailure.bundledPythonHomeMissing("no private Frameworks URL")
        }
        let home = frameworks
            .appendingPathComponent("Python.framework", isDirectory: true)
            .appendingPathComponent("Versions/3.13", isDirectory: true)
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(
            atPath: home.path,
            isDirectory: &isDirectory
        ), isDirectory.boolValue else {
            throw ConsumerFailure.bundledPythonHomeMissing(home.path)
        }
        guard setenv("PYTHONHOME", home.path, 1) == 0 else {
            throw ConsumerFailure.pythonHomeEnvironmentFailed(errno)
        }
    }

    private static func publishLaunchServicesReceiptIfRequested() throws {
        guard let nonce = ProcessInfo.processInfo.environment[
            "SWIFTPYTHON_LAUNCH_SERVICES_RECEIPT_NONCE"
        ] else {
            return
        }
        let allowed = Set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_".utf8
        )
        guard (16...128).contains(nonce.utf8.count),
              nonce.utf8.allSatisfy({ allowed.contains($0) }) else {
            throw ConsumerFailure.invalidLaunchServicesReceiptNonce(nonce)
        }
        try FileHandle.standardOutput.write(
            contentsOf: Data("swiftpython-launch-services-success=\(nonce)\n".utf8)
        )
    }

    private static func run() async throws {
        try assertPrivateEngineLoadedOnce()
        if ProcessInfo.processInfo.environment["SWIFTPYTHON_SKIP_IN_PROCESS"] != "1" {
            let version: String = try await Python.run {
                try String(pythonObject: Python.sys.version)
            }
            print("Python \(version)")
        } else {
            print("in-process Python skipped for sandbox worker-path proof")
        }

        try await withProcessPool(workers: 1) { pool in
            let value: Double = try await pool.invokeResult(
                module: "math",
                function: "sqrt",
                args: [.python(144.0)]
            )
            print("math.sqrt(144.0) = \(value)")

            let session = try await pool.openDuplexSession(
                handler: .eval(
                    code: """
                    from swift_duplex import InputFrame
                    def run(session):
                        session.ready({"consumer": "commercial"})
                        for event in session.iter_input():
                            if isinstance(event, InputFrame):
                                session.output.send(
                                    bytes(event.buffer),
                                    processed_input_through=event.sequence,
                                )
                        session.output.finish()
                    """,
                    entrypoint: "run"
                )
            )
            let payload = Data("full-duplex-commercial-smoke".utf8)
            try await session.input.send(DuplexInputFrame(payload: payload))
            try await session.input.finish()
            var outputs = session.output.makeAsyncIterator()
            guard let frame = try await outputs.next() else {
                throw ConsumerFailure.missingOutput
            }
            guard frame.buffer.copyData() == payload else {
                throw ConsumerFailure.outputMismatch
            }
            try await session.acknowledgeOutput(
                consumedThrough: DuplexPosition(
                    sequence: frame.position.sequence,
                    byteOffset: frame.buffer.count
                )
            )
            guard try await outputs.next() == nil else {
                throw ConsumerFailure.extraOutput
            }
            let result = try await session.result()
            guard result.terminal == .completed else {
                throw ConsumerFailure.badTerminal
            }
            await session.close()
            print("full duplex loopback = \(payload.count) bytes")

            try await runLogicalMessageSmoke(pool: pool)
            try await runSharedArenaMetalSmoke(pool: pool)
        }

        try await runPublicMetalQuarantineSmoke()

        let format = try DuplexAudioFormat(
            sampleRate: 24_000,
            channels: 1,
            sampleType: .signedInteger16,
            interleaving: .interleaved
        )
        let ledger = DuplexCopyLedger()
        print(
            "optional adapters = \(format.bytesPerFrame) bytes/frame, "
                + "\(ledger.snapshot.observedBytes) Metal bytes observed"
        )
        try await runAudioProbeIfConfigured(wireFormat: format)

        try await runVMReleaseGateIfConfigured()
    }

    private enum AudioProbeGate: String {
        case off
        case containment
        case ready
    }

    private static func runAudioProbeIfConfigured(
        wireFormat: DuplexAudioFormat
    ) async throws {
        let rawGate = ProcessInfo.processInfo.environment[
            "SWIFTPYTHON_AUDIO_PROBE_GATE"
        ] ?? "off"
        guard let gate = AudioProbeGate(rawValue: rawGate) else {
            throw ConsumerFailure.invalidAudioProbeGate(rawGate)
        }
        guard gate != .off else {
            print("audio helper = embedded/signature contract only (device gate off)")
            return
        }

        guard await requestParentMicrophonePermission(),
              DuplexAudioHardwareProbeLauncher.permissionState == .granted else {
            throw ConsumerFailure.audioProbePermissionNotGranted
        }
        let configuration = try DuplexAudioHardwareProbeConfiguration(
            wireFormat: wireFormat,
            durationSeconds: 0.5,
            timeoutSeconds: 15,
            requiresNonIdentityCaptureConversion: true
        )
        switch try await DuplexAudioHardwareProbeLauncher.run(
            configuration: configuration
        ) {
        case let .ready(report):
            let metrics = report.metrics
            guard report.schemaVersion == 1,
                  report.engineScope == .isolatedChildProcess,
                  report.sharedEngine,
                  report.simultaneousCaptureAndPlayback,
                  report.outputMutedForSafety,
                  metrics.captureHostTimestampFallbackCount == 0,
                  metrics.captureClockResetCount == 0,
                  metrics.captureHostClockResetCount == 0 else {
                throw ConsumerFailure.audioProbeReadyInvariantFailed
            }
            if gate == .ready {
                print(
                    "audio helper release-ready = request \(report.requestID), "
                        + "playback invalid sample times "
                        + "\(metrics.playbackInvalidSampleTimeCount)"
                )
            } else {
                print(
                    "audio helper containment observed a ready receipt "
                        + "(NOT RELEASE-GATE EVIDENCE): request "
                        + "\(report.requestID)"
                )
            }
        case let .notReady(failure):
            guard gate == .containment,
                  isAcceptableContainmentOnlyFailure(failure) else {
                throw ConsumerFailure.audioProbeNotReady(
                    String(describing: failure)
                )
            }
            print(
                "audio helper containment = NOT READY (not release-ready): "
                    + String(describing: failure)
            )
        @unknown default:
            throw ConsumerFailure.audioProbeUnknownOutcome
        }
    }

    private static func isAcceptableContainmentOnlyFailure(
        _ failure: DuplexAudioHardwareProbeFailure
    ) -> Bool {
        switch failure {
        case .helperTimedOut:
            return true
        case let .helperReported(code: code, stage: _, restart: _, context: _):
            switch code {
            case .permissionNotGranted,
                 .noInputDevice,
                 .noOutputDevice,
                 .invalidInputFormat,
                 .invalidOutputFormat,
                 .captureConversionUnavailable,
                 .playbackConversionUnavailable,
                 .engineStartFailed,
                 .routeChanged,
                 .captureFailed,
                 .playbackFailed:
                return true
            case .shutdownFailed, .invariantFailed, .internalFailure:
                return false
            }
        default:
            return false
        }
    }

    private static func requestParentMicrophonePermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return true
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    continuation.resume(returning: granted)
                }
            }
        case .denied, .restricted:
            return false
        @unknown default:
            return false
        }
    }

    private static func assertPrivateEngineLoadedOnce() throws {
        var paths: [String] = []
        for index in 0..<_dyld_image_count() {
            guard let name = _dyld_get_image_name(index) else { continue }
            let path = String(cString: name)
            if path.contains("SwiftPythonEngine.framework/")
                    && path.hasSuffix("/SwiftPythonEngine") {
                paths.append(path)
            }
        }
        guard paths.count == 1 else {
            throw ConsumerFailure.privateEngineImageCount(paths)
        }
        print("private Engine images = 1")
    }

    private static func runLogicalMessageSmoke(
        pool: PythonProcessPool
    ) async throws {
        let logicalBytes = 10 * 1_024 * 1_024 + 137
        let chunkBytes = 256 * 1_024
        let payload = Data(repeating: 0xA7, count: logicalBytes)
        let requirements = DuplexSessionRequirements.messages
        let format = DuplexFormat(
            "video/hevc",
            metadata: [
                "codec": "hevc-main",
                "display": "targeted",
                "width": "4096",
                "height": "2304",
            ]
        )
        let session = try await pool.openDuplexSession(
            handler: .eval(
                code: """
                import hashlib
                from swift_duplex import InputFinished

                def run(session):
                    session.ready({"consumer": "commercial-message"})
                    message = session.receive_message()
                    assert message.total_bytes == \(logicalBytes)
                    assert message.format == "video/hevc"
                    assert message.format_metadata == {
                        "codec": "hevc-main",
                        "display": "targeted",
                        "width": "4096",
                        "height": "2304",
                    }
                    assert message.flags & 1 == 1
                    assert message.timestamp_ns > 0
                    assert message.storage_route == "inline"
                    digest = hashlib.sha256()
                    chunks = 0
                    last_sequence = 0
                    for chunk in message.chunks():
                        assert chunk.byte_count <= \(chunkBytes)
                        assert chunk.flags & 1 == 1
                        assert chunk.format == "video/hevc"
                        assert chunk.format_metadata["display"] == "targeted"
                        digest.update(chunk.buffer)
                        chunks += 1
                        last_sequence = chunk.sequence
                    assert chunks == \((logicalBytes + chunkBytes - 1) / chunkBytes)
                    assert isinstance(session.receive(), InputFinished)
                    session.output.send(
                        digest.digest(),
                        processed_input_through=last_sequence,
                    )
                    session.output.finish()
                """,
                entrypoint: "run"
            ),
            options: DuplexOptions(
                inputFormat: format,
                limits: DuplexLimits(
                    maximumFrameBytes: 1 * 1_024 * 1_024,
                    maximumLogicalMessageBytes: 12 * 1_024 * 1_024,
                    preferredMessageChunkBytes: chunkBytes,
                    inputCreditBytes: 1 * 1_024 * 1_024,
                    inputCreditFrames: 4
                ),
                requirements: requirements
            )
        )
        guard session.profile.maximumMessageBytes >= logicalBytes else {
            throw ConsumerFailure.badNegotiatedMessageConfiguration
        }
        try await session.input.sendMessage(
            payload,
            timestamp: ContinuousClock.now,
            format: format,
            flags: [.independent]
        )
        try await session.input.finish()
        var outputs = session.output.makeAsyncIterator()
        guard let digestFrame = try await outputs.next() else {
            throw ConsumerFailure.missingOutput
        }
        guard digestFrame.buffer.copyData()
                == Data(SHA256.hash(data: payload)) else {
            throw ConsumerFailure.logicalMessageDigestMismatch
        }
        try await session.acknowledgeOutput(
            consumedThrough: DuplexPosition(
                sequence: digestFrame.position.sequence,
                byteOffset: digestFrame.buffer.count
            )
        )
        guard try await outputs.next() == nil else {
            throw ConsumerFailure.extraOutput
        }
        guard try await session.result().terminal == .completed else {
            throw ConsumerFailure.badTerminal
        }
        await session.close()
        print(
            "logical message = \(logicalBytes) bytes / "
                + "\((logicalBytes + chunkBytes - 1) / chunkBytes) chunks"
        )
    }

    private static func runSharedArenaMetalSmoke(
        pool: PythonProcessPool
    ) async throws {
        guard let device = MTLCreateSystemDefaultDevice(),
              let queue = device.makeCommandQueue() else {
            throw ConsumerFailure.missingMetalDevice
        }
        let slotBytes = Int(getpagesize())
        var options = DuplexOptions.default
        options.requirements = .managedBuffers
        options.limits = DuplexLimits(
            maximumFrameBytes: slotBytes,
            maximumLogicalMessageBytes: slotBytes,
            preferredMessageChunkBytes: slotBytes,
            inputCreditBytes: slotBytes,
            inputCreditFrames: 1
        )
        options.managedBuffers = ManagedBufferConfiguration(
            preset: .memoryEfficient,
            maximumBufferBytes: slotBytes,
            maximumBufferedBytes: slotBytes
        )
        let session = try await pool.openDuplexSession(
            handler: .eval(
                code: """
                import gc
                from swift_duplex import ApplicationControl, InputFinished

                def run(session):
                    session.ready({"consumer": "commercial-arena"})
                    first = session.receive_message()
                    chunks = first.chunks()
                    chunk = next(chunks)
                    assert chunk.storage_route == "shared_arena"
                    assert chunk.byte_count == \(slotBytes)
                    held = memoryview(chunk.buffer)
                    assert held[0] == 0x6D and held[-1] == 0x6D
                    del chunk, chunks, first
                    gc.collect()
                    session.send_event("arena-python-held")
                    control = session.receive()
                    assert isinstance(control, ApplicationControl)
                    assert control.kind == "release-arena-python"
                    held.release()
                    del held, control
                    gc.collect()
                    session.send_event("arena-python-released")

                    second = session.receive_message()
                    assert second.storage_route == "shared_arena"
                    assert second.read(max_bytes=\(slotBytes)) == b"n" * \(slotBytes)
                    assert isinstance(session.receive(), InputFinished)
                    session.output.finish()
                """,
                entrypoint: "run"
            ),
            options: options
        )
        let initial = try requireManagedBufferStatus(session)
        guard initial.capacityBytes == slotBytes,
              initial.bytesInUse == 0,
              initial.isAvailable else {
            throw ConsumerFailure.arenaInitialStateMismatch
        }
        let coreLease = try await session.input.acquireManagedBuffer(
            byteCount: slotBytes
        )
        try coreLease.withUnsafeMutableBytes { bytes in
            for index in bytes.indices { bytes[index] = 0x6D }
        }
        let ledger = DuplexCopyLedger()
        let metalLease = try coreLease.makeMetalBufferLease(
            device: device,
            access: .cpuWritesGPUReads,
            ledger: ledger
        )
        var pointerIdentity = false
        try coreLease.withUnsafeMutableBytes { bytes in
            pointerIdentity = bytes.baseAddress == metalLease.buffer.contents()
        }
        guard pointerIdentity else {
            throw ConsumerFailure.metalPointerIdentityMismatch
        }
        guard let command = queue.makeCommandBuffer() else {
            throw ConsumerFailure.missingMetalCommandBuffer
        }
        try metalLease.retainUntilCompleted(by: command)
        try await session.input.sendMessage(
            coreLease,
            flags: [.independent]
        )

        var events = session.events.makeAsyncIterator()
        try await waitForApplication(
            "arena-python-held",
            events: &events
        )
        let held = try requireManagedBufferStatus(session)
        guard !held.isAvailable,
              held.bytesInUse == slotBytes else {
            throw ConsumerFailure.arenaLeaseReleasedTooEarly
        }
        try await session.sendControl(
            DuplexApplicationControl(kind: "release-arena-python")
        )
        try await waitForApplication(
            "arena-python-released",
            events: &events
        )
        let peerReleaseDeadline = ContinuousClock.now + .seconds(2)
        while ContinuousClock.now < peerReleaseDeadline,
              session.managedBufferStatus?.bytesInUse != 0 {
            try await Task.sleep(for: .milliseconds(10))
        }
        let peerReleased = try requireManagedBufferStatus(session)
        guard peerReleased.bytesInUse == 0,
              !peerReleased.isAvailable else {
            throw ConsumerFailure.arenaPeerReleaseDidNotPreserveMetalLease
        }

        let blockedAcquire = Task {
            try await session.input.acquireManagedBuffer(
                byteCount: slotBytes
            )
        }
        try await Task.sleep(for: .milliseconds(100))
        guard session.managedBufferStatus?.isAvailable == false else {
            throw ConsumerFailure.arenaLeaseReleasedTooEarly
        }
        try metalLease.finishMetalAccess()
        command.commit()
        await command.completed()
        guard command.status == .completed else {
            throw ConsumerFailure.metalCommandFailed
        }
        let second = try await blockedAcquire.value
        try second.withUnsafeMutableBytes { bytes in
            for index in bytes.indices { bytes[index] = 0x6E }
        }
        try await session.input.sendMessage(second)
        try await session.input.finish()
        var output = session.output.makeAsyncIterator()
        guard try await output.next() == nil else {
            throw ConsumerFailure.extraOutput
        }
        guard try await session.result().terminal == .completed else {
            throw ConsumerFailure.badTerminal
        }
        let final = try requireManagedBufferStatus(session)
        guard final.capacityBytes == initial.capacityBytes,
              final.bytesInUse == 0,
              final.isAvailable,
              ledger.snapshot.entries.contains(where: {
                  $0.route == .arenaSharedNoCopy
                      && $0.status == .zeroCopy
                      && $0.logicalBytes == slotBytes
              }) else {
            throw ConsumerFailure.arenaFinalLeaseOrLedgerMismatch
        }
        await session.close()
        print(
            "shared arena = pointer-identical Metal mapping, "
                + "Python/GPU final-lease turnover, \(final.capacityBytes) managed bytes"
        )
    }

    private static func waitForApplication(
        _ expected: String,
        events: inout DuplexEvents.AsyncIterator
    ) async throws {
        while let event = try await events.next() {
            if case .application(let application) = event,
               application.kind == expected {
                return
            }
        }
        throw ConsumerFailure.missingApplicationEvent(expected)
    }

    private static func runPublicMetalQuarantineSmoke() async throws {
        guard let device = MTLCreateSystemDefaultDevice() else {
            throw ConsumerFailure.missingMetalDevice
        }
        let pool = try DuplexMetalRegionPool(
            device: device,
            storage: .managed,
            configuration: DuplexMetalPoolConfiguration(
                regionCount: 1,
                regionCapacity: 4_096
            )
        )
        let lease = try await pool.acquire(
            logicalByteCount: 64,
            access: .gpuWritesCPUReads
        )
        let snapshot = await pool.cancelAndQuarantineOutstanding(
            grace: .zero
        )
        guard snapshot.quarantinedRegions == 1,
              snapshot.availableRegions == 0,
              try pool.tryAcquire(
                logicalByteCount: 1,
                access: .cpuWritesGPUReads
              ) == nil else {
            throw ConsumerFailure.metalQuarantineWasReusable
        }
        withExtendedLifetime(lease) {}
        print("Metal quarantine = outstanding region cannot be reused")
    }

    private static func runVMReleaseGateIfConfigured() async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let baseImage = environment["SWIFTPYTHON_VM_BASE_IMAGE"] else {
            return
        }
        guard let snapshot = environment["SWIFTPYTHON_VM_SNAPSHOT"],
              let restoreSecretPath = environment["SWIFTPYTHON_VM_RESTORE_SECRET"],
              let cloneDir = environment["SWIFTPYTHON_VM_CLONE_DIR"],
              let iterationsText = environment["SWIFTPYTHON_VM_ITERATIONS"],
              let iterations = Int(iterationsText),
              iterations > 0 else {
            throw ConsumerFailure.vmGate("incomplete VM release-gate environment")
        }
        let restoreSecret = try Data(
            contentsOf: URL(fileURLWithPath: restoreSecretPath)
        )
        guard !restoreSecret.isEmpty else {
            throw ConsumerFailure.vmGate("empty VM snapshot restore secret")
        }
        let minimumVersion = environment["SWIFTPYTHON_RELEASE_VERSION"]
            ?? "0.6.0-duplex.3"

        for iteration in 1...iterations {
            let tenantID = SandboxTenantID(
                rawValue: "commercial-notary-\(iteration)-\(UUID().uuidString.prefix(8))"
            )
            let sandbox = try await SandboxProviders.system.makePool(
                configuration: SandboxConfiguration(
                    runtimeAsset: URL(fileURLWithPath: baseImage),
                    storageDirectory: URL(fileURLWithPath: cloneDir),
                    compute: .balanced,
                    startup: .accelerated(
                        checkpoint: URL(fileURLWithPath: snapshot),
                        credential: SandboxCredential(sealedBytes: restoreSecret)
                    ),
                    network: .denied,
                    workersPerSandbox: 1,
                    minimumRuntimeVersion: minimumVersion,
                    integrity: .strict
                )
            )
            var acquired: SandboxTenant?
            do {
                let tenant = try await sandbox.acquire(tenantID: tenantID)
                acquired = tenant
                guard tenant.startupMode == .accelerated else {
                    throw ConsumerFailure.vmGate("cold boot strategy returned")
                }
                try await runVMWorkloadMatrix(
                    sandbox: sandbox,
                    tenant: tenant,
                    iteration: iteration
                )
                try await sandbox.release(tenant, force: true)
                acquired = nil
                await sandbox.shutdown()
                guard await sandbox.activeTenantIDs().isEmpty else {
                    throw ConsumerFailure.vmGate("tenant remained active after release")
                }
                print(
                    "notarized accelerated sandbox \(iteration)/\(iterations) = PASS"
                )
            } catch {
                if let acquired {
                    try? await sandbox.release(acquired, force: true)
                }
                await sandbox.shutdown()
                throw error
            }
        }
    }

    private static func runVMWorkloadMatrix(
        sandbox: SandboxPool,
        tenant: SandboxTenant,
        iteration: Int
    ) async throws {
        let evaluated: Int = try await tenant.processPool.evalResult(
            "6 * 7",
            timeout: 15
        )
        guard evaluated == 42 else {
            throw ConsumerFailure.vmGate("VM eval returned \(evaluated)")
        }

        let stream: CancellableStream<Int> = try await tenant.processPool
            .evalStream("range(4)")
        var streamed: [Int] = []
        for try await value in stream { streamed.append(value) }
        guard streamed == [0, 1, 2, 3] else {
            throw ConsumerFailure.vmGate("VM Python stream mismatch")
        }

        let callbackName = "commercial_vm_\(iteration)_\(UUID().uuidString.replacingOccurrences(of: "-", with: ""))"
        let registration = try await tenant.processPool.registerCallback(
            name: callbackName
        ) { @Sendable (value: Int) -> Int in
            value * 2
        }
        let callbackResult: Int = try await tenant.processPool.evalResult(
            """
            import swift_bridge
            swift_bridge.call("\(callbackName)", 21)
            """,
            timeout: 15
        )
        try await tenant.processPool.unregisterCallback(name: callbackName)
        _ = registration
        guard callbackResult == 42 else {
            throw ConsumerFailure.vmGate("VM callback mismatch")
        }

        let capture = try await sandbox.execShell(
            tenantID: tenant.id,
            "printf 'capture-out'; printf 'capture-err' >&2",
            options: ExecStreamOptions(timeout: 15, maxOutputBytes: 4_096)
        )
        guard capture.exitCode == 0,
              String(decoding: capture.stdout, as: UTF8.self) == "capture-out",
              String(decoding: capture.stderr, as: UTF8.self) == "capture-err" else {
            throw ConsumerFailure.vmGate("VM shell capture mismatch")
        }

        let streamedShell = try await sandbox.execShellStream(
            tenantID: tenant.id,
            "printf 'stream-a'; sleep 0.05; printf 'stream-b'; printf 'stream-err' >&2",
            options: ExecStreamOptions(timeout: 15, maxOutputBytes: 4_096)
        )
        let streamedCollection = try await collectSandboxSession(streamedShell)
        guard streamedCollection.result.exitCode == 0,
              String(decoding: streamedCollection.stdout, as: UTF8.self)
                == "stream-astream-b",
              String(decoding: streamedCollection.stderr, as: UTF8.self)
                == "stream-err" else {
            throw ConsumerFailure.vmGate("VM shell stream mismatch")
        }

        let pty = try await sandbox.execShellPTY(
            tenantID: tenant.id,
            "IFS= read -r token; printf 'PTY:%s:' \"$token\"; stty size",
            options: ExecPTYOptions(
                timeout: 15,
                maxOutputBytes: 4_096,
                initialSize: TerminalSize(columns: 80, rows: 24)
            )
        )
        let ptyCollection = Task { try await collectSandboxSession(pty) }
        try await pty.resize(to: TerminalSize(columns: 101, rows: 33))
        try await pty.sendStdin(Data("commercial-vm-pty\n".utf8))
        try await pty.finishStdin()
        let collectedPTY = try await ptyCollection.value
        let ptyText = String(decoding: collectedPTY.stdout, as: UTF8.self)
        guard collectedPTY.result.exitCode == 0,
              ptyText.contains("PTY:commercial-vm-pty:"),
              ptyText.contains("33 101") else {
            throw ConsumerFailure.vmGate("VM PTY mismatch")
        }

        try await runVMLogicalMessage(pool: tenant.processPool)
        try await requireVMArenaIngressRejection(pool: tenant.processPool)
    }

    private static func collectSandboxSession(
        _ session: SandboxExecSession
    ) async throws -> (
        stdout: Data,
        stderr: Data,
        result: SandboxShellResult
    ) {
        var stdout = Data()
        var stderr = Data()
        for try await chunk in session.chunks {
            switch chunk.stream {
            case .stdout: stdout.append(chunk.bytes)
            case .stderr: stderr.append(chunk.bytes)
            @unknown default:
                throw ConsumerFailure.vmGate("unknown VM shell stream")
            }
        }
        return (stdout, stderr, try await session.result.value)
    }

    private static func runVMLogicalMessage(
        pool: PythonProcessPool
    ) async throws {
        let byteCount = 2 * 1_024 * 1_024 + 137
        let chunkBytes = 128 * 1_024
        let payload = Data(repeating: 0xA7, count: byteCount)
        let format = DuplexFormat(
            "video/hevc",
            metadata: ["profile": "main", "gate": "notarized-vm"]
        )
        let requirements = DuplexSessionRequirements.messages
        var options = DuplexOptions.default
        options.inputFormat = format
        options.requirements = requirements
        options.limits.maximumFrameBytes = 256 * 1_024
        options.limits.maximumLogicalMessageBytes = 3 * 1_024 * 1_024
        options.limits.preferredMessageChunkBytes = chunkBytes
        options.limits.inputCreditBytes = 512 * 1_024
        options.limits.inputCreditFrames = 4
        let session = try await pool.openDuplexSession(
            handler: .eval(
                code: """
                import hashlib
                def run(session):
                    session.ready({"gate": "notarized-vm"})
                    message = session.receive_message()
                    assert message.total_bytes == \(byteCount)
                    assert message.format_metadata["gate"] == "notarized-vm"
                    digest = hashlib.sha256()
                    last_sequence = 0
                    for chunk in message.chunks():
                        digest.update(chunk.buffer)
                        last_sequence = chunk.sequence
                    session.receive()
                    session.output.send(
                        digest.digest(),
                        processed_input_through=last_sequence,
                    )
                    session.output.finish()
                """,
                entrypoint: "run"
            ),
            options: options
        )
        do {
            try await session.input.sendMessage(
                payload,
                format: format,
                flags: [.independent]
            )
            try await session.input.finish()
            var output = session.output.makeAsyncIterator()
            guard let digest = try await output.next(),
                  digest.buffer.copyData() == Data(SHA256.hash(data: payload)) else {
                throw ConsumerFailure.vmGate("VM logical-message digest mismatch")
            }
            try await session.acknowledgeOutput(
                consumedThrough: DuplexPosition(
                    sequence: digest.position.sequence,
                    byteOffset: digest.buffer.count
                )
            )
            guard try await output.next() == nil,
                  try await session.result().terminal == .completed else {
                throw ConsumerFailure.vmGate("VM logical-message terminal mismatch")
            }
            await session.close()
        } catch {
            await session.cancel(reason: .user)
            await session.close()
            throw error
        }
    }

    private static func requireVMArenaIngressRejection(
        pool: PythonProcessPool
    ) async throws {
        var options = DuplexOptions.default
        options.requirements = .managedBuffers
        options.managedBuffers = ManagedBufferConfiguration(
            preset: .memoryEfficient,
            maximumBufferBytes: 4_096,
            maximumBufferedBytes: 4_096
        )
        do {
            let unexpected = try await pool.openDuplexSession(
                handler: .function(
                    module: "must_not_import_for_vm_arena_gate",
                    name: "run"
                ),
                options: options
            )
            await unexpected.close()
            throw ConsumerFailure.vmGate("VM accepted local arena ingress")
        } catch let failure as DuplexFailure {
            guard failure.code == .featureUnavailable,
                  failure.origin == .host else {
                throw ConsumerFailure.vmGate("wrong VM arena rejection: \(failure)")
            }
        }
    }

    private static func requireManagedBufferStatus(
        _ session: PythonDuplexSession
    ) throws -> ManagedBufferStatus {
        guard let snapshot = session.managedBufferStatus else {
            throw ConsumerFailure.missingArenaSnapshot
        }
        return snapshot
    }
}

enum ConsumerFailure: Error {
    case missingOutput
    case outputMismatch
    case extraOutput
    case badTerminal
    case badNegotiatedMessageConfiguration
    case logicalMessageDigestMismatch
    case missingMetalDevice
    case missingMetalCommandBuffer
    case metalPointerIdentityMismatch
    case metalCommandFailed
    case privateEngineImageCount([String])
    case arenaInitialStateMismatch
    case arenaLeaseReleasedTooEarly
    case arenaPeerReleaseDidNotPreserveMetalLease
    case arenaFinalLeaseOrLedgerMismatch
    case metalQuarantineWasReusable
    case missingArenaSnapshot
    case missingApplicationEvent(String)
    case vmGate(String)
    case invalidAudioProbeGate(String)
    case audioProbePermissionNotGranted
    case audioProbeNotReady(String)
    case audioProbeReadyInvariantFailed
    case audioProbeUnknownOutcome
    case bundledPythonHomeMissing(String)
    case pythonHomeEnvironmentFailed(Int32)
    case invalidLaunchServicesReceiptNonce(String)
}
EOF
}

for module in SwiftPythonRuntime SwiftPythonAudioInterop SwiftPythonMetalInterop; do
    test -d "$REPO_DIR/$module.xcframework/macos-"*"/Headers/$module.swiftmodule"
    test -d "$REPO_DIR/$module.xcframework/macos-"*"/$module.swiftmodule"
done
if find "$REPO_DIR/SwiftPythonEngine.xcframework" -type f \( \
    -name '*.swiftinterface' -o \
    -name '*.swiftmodule' -o \
    -name '*.swiftdoc' -o \
    -name '*.swiftsourceinfo' -o \
    -name '*.swift' -o \
    -name '*.abi.json' \
\) -print -quit | grep -q .; then
    echo "Private Engine unexpectedly exposes Swift module metadata or source." >&2
    exit 1
fi

SPM_DIR="$WORK_DIR/spm"
XCODE_DIR="$WORK_DIR/xcode"
write_local_binary_package
write_consumer_package "$SPM_DIR"
write_consumer_package "$XCODE_DIR"

export SWIFTPYTHON_WORKER_PATH="$REPO_DIR/SwiftPythonWorker"

echo "=== SwiftPM HeadersPath consumer ==="
SWIFTPYTHON_AUDIO_PROBE_GATE=off swift run --package-path "$SPM_DIR"

echo "=== xcodebuild slice-root consumer ==="
(
    cd "$XCODE_DIR"
    xcodebuild \
        -quiet \
        -scheme ConsumerSmoke \
        -destination "platform=macOS,arch=arm64" \
        -derivedDataPath "$WORK_DIR/DerivedData" \
        CODE_SIGNING_ALLOWED=NO \
        build
)
DYLD_FRAMEWORK_PATH="$(dirname "$ENGINE_FRAMEWORK")" \
    SWIFTPYTHON_AUDIO_PROBE_GATE=off \
    "$WORK_DIR/DerivedData/Build/Products/Debug/ConsumerSmoke"

DEVELOPER_ID="$(
    security find-identity -v -p codesigning \
        | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' \
        | head -1
)"
if [ -z "$DEVELOPER_ID" ]; then
    echo "A Developer ID Application identity is required." >&2
    exit 1
fi

PARENT_SANDBOX_ENTITLEMENTS="$WORK_DIR/ConsumerSandbox.entitlements"
cat > "$PARENT_SANDBOX_ENTITLEMENTS" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.app-sandbox</key>
  <true/>
  <key>com.apple.security.network.client</key>
  <true/>
  <key>com.apple.security.network.server</key>
  <true/>
  <key>com.apple.security.device.audio-input</key>
  <true/>
</dict>
</plist>
EOF

PARENT_VM_ENTITLEMENTS="$WORK_DIR/ConsumerVM.entitlements"
cat > "$PARENT_VM_ENTITLEMENTS" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
  <true/>
  <key>com.apple.security.cs.disable-library-validation</key>
  <true/>
  <key>com.apple.security.virtualization</key>
  <true/>
</dict>
</plist>
EOF

assert_bundle_remained_sealed() {
    local app="$1"
    if find "$app" -type d -name __pycache__ -print -quit | grep -q .; then
        echo "Python __pycache__ appeared in sealed app: $app" >&2
        exit 1
    fi
    if find "$app" -type f -name '*.pyc' -print -quit | grep -q .; then
        echo "Python bytecode appeared in sealed app: $app" >&2
        exit 1
    fi
    codesign --verify --deep --strict --verbose=2 "$app"
}

bundle_content_digest() {
    python3 - "$1" <<'PY'
import hashlib
import os
import stat
import sys

root = os.path.abspath(sys.argv[1])
digest = hashlib.sha256()
for directory, directories, files in os.walk(root, followlinks=False):
    directories.sort()
    files.sort()
    for name in directories + files:
        path = os.path.join(directory, name)
        relative = os.path.relpath(path, root).encode()
        metadata = os.lstat(path)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(stat.S_IMODE(metadata.st_mode).to_bytes(4, "big"))
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path).encode()
            digest.update(len(target).to_bytes(8, "big"))
            digest.update(target)
        elif stat.S_ISREG(metadata.st_mode):
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
print(digest.hexdigest())
PY
}

assert_bundle_content_unchanged() {
    local app="$1"
    local expected="$2"
    local observed
    observed="$(bundle_content_digest "$app")"
    if [ "$observed" != "$expected" ]; then
        echo "Sealed app content changed after execution: $app" >&2
        echo "expected=$expected observed=$observed" >&2
        exit 1
    fi
}

assert_signed_entitlements_match() {
    local executable="$1"
    local expected_plist="$2"
    python3 - "$executable" "$expected_plist" <<'PY'
import plistlib
import subprocess
import sys

completed = subprocess.run(
    ["codesign", "-d", "--entitlements", ":-", sys.argv[1]],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    check=True,
)
observed = plistlib.loads(completed.stdout)
with open(sys.argv[2], "rb") as handle:
    expected = plistlib.load(handle)
if observed != expected:
    raise SystemExit(
        f"signed entitlements mismatch for {sys.argv[1]}: "
        f"observed={observed!r} expected={expected!r}"
    )
PY
}

assert_parent_audio_policy() {
    local app="$1"
    local expected_sandbox="$2"
    python3 - "$app" "$expected_sandbox" <<'PY'
import pathlib
import plistlib
import subprocess
import sys

app = pathlib.Path(sys.argv[1])
expected_sandbox = sys.argv[2] == "1"
with (app / "Contents" / "Info.plist").open("rb") as handle:
    info = plistlib.load(handle)
purpose = info.get("NSMicrophoneUsageDescription")
if not isinstance(purpose, str) or not purpose.strip():
    raise SystemExit(f"parent microphone purpose string is missing: {app}")
completed = subprocess.run(
    ["codesign", "-d", "--entitlements", ":-", str(app)],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    check=True,
)
entitlements = plistlib.loads(completed.stdout)
observed_sandbox = entitlements.get("com.apple.security.app-sandbox") is True
if observed_sandbox != expected_sandbox:
    raise SystemExit(
        f"parent sandbox state mismatch for {app}: "
        f"expected={expected_sandbox} observed={observed_sandbox}"
    )
if expected_sandbox \
        and entitlements.get("com.apple.security.device.audio-input") is not True:
    raise SystemExit(f"sandbox parent lacks audio-input entitlement: {app}")
PY
}

assert_nested_probe_identity() {
    local app="$1"
    local helper="$app/Contents/MacOS/SwiftPythonAudioProbe"
    python3 - "$app" "$helper" <<'PY'
import os
import pathlib
import re
import stat
import subprocess
import sys

app = pathlib.Path(sys.argv[1])
helper = pathlib.Path(sys.argv[2])
metadata = helper.lstat()
if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
    raise SystemExit(f"nested audio probe is not one regular non-symlink file: {helper}")
if not os.access(helper, os.X_OK):
    raise SystemExit(f"nested audio probe is not executable: {helper}")
expected_parent = (app / "Contents" / "MacOS").resolve(strict=True)
if helper.parent.resolve(strict=True) != expected_parent \
        or helper.name != "SwiftPythonAudioProbe":
    raise SystemExit(f"nested audio probe is not at the fixed bundle path: {helper}")

def signature(path: pathlib.Path) -> str:
    return subprocess.run(
        ["codesign", "-dvv", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    ).stdout

def field(details: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}=(.+)$", details, re.MULTILINE)
    if match is None or not match.group(1).strip():
        raise SystemExit(f"signed {name} is missing")
    return match.group(1).strip()

parent_details = signature(app)
helper_details = signature(helper)
parent_identifier = field(parent_details, "Identifier")
helper_identifier = field(helper_details, "Identifier")
expected_identifier = parent_identifier + ".SwiftPythonAudioProbe"
if helper_identifier != expected_identifier:
    raise SystemExit(
        f"nested audio probe identifier mismatch: "
        f"expected={expected_identifier} observed={helper_identifier}"
    )
parent_team = field(parent_details, "TeamIdentifier")
helper_team = field(helper_details, "TeamIdentifier")
if parent_team == "not set" or helper_team != parent_team:
    raise SystemExit(
        f"nested audio probe team mismatch: "
        f"parent={parent_team} helper={helper_team}"
    )
PY
}

make_app() {
    local mode="$1"
    local identity="$2"
    local parent_entitlements="$3"
    local worker_entitlements="$4"
    local probe_entitlements="$5"
    local app="$WORK_DIR/ConsumerSmoke-$mode.app"
    local macos="$app/Contents/MacOS"
    local frameworks="$app/Contents/Frameworks"
    local python_framework="$frameworks/Python.framework"
    local embedded_engine="$frameworks/SwiftPythonEngine.framework"
    local embedded_engine_binary="$embedded_engine/Versions/A/SwiftPythonEngine"
    local python_home_for_app="$PYTHON_HOME_DIR"
    local effective_audio_probe_gate="$AUDIO_PROBE_GATE"
    local mode_identifier=d
    if [ "$mode" = sandbox ]; then
        mode_identifier=s
    elif [ "$mode" = virtualization ]; then
        mode_identifier=v
        # The VM fixture owns VM behavior only. Developer and sandbox fixtures
        # exercise the device-bound launcher gate for this same candidate.
        effective_audio_probe_gate=off
    fi
    local bundle_identifier="ai.bestbyte.sp.$mode_identifier.$SMOKE_ID_SUFFIX"
    local skip_in_process=0
    if [ "$mode" = sandbox ]; then
        skip_in_process=1
        python_home_for_app="$frameworks/Python.framework/Versions/3.13"
    fi
    mkdir -p "$macos"
    cp "$WORK_DIR/DerivedData/Build/Products/Debug/ConsumerSmoke" \
        "$macos/ConsumerSmoke"
    cp "$REPO_DIR/SwiftPythonWorker" "$macos/SwiftPythonWorker"
    cp "$AUDIO_PROBE" "$macos/SwiftPythonAudioProbe"
    cmp -s "$AUDIO_PROBE" "$macos/SwiftPythonAudioProbe" \
        || { echo "Embedded audio probe does not match staged input bytes." >&2; exit 1; }
    ditto "$ENGINE_FRAMEWORK" "$embedded_engine"
    if ! otool -l "$macos/ConsumerSmoke" \
        | grep -F '@executable_path/../Frameworks' >/dev/null; then
        install_name_tool -add_rpath \
            '@executable_path/../Frameworks' \
            "$macos/ConsumerSmoke"
    fi
    if [ "$mode" = sandbox ]; then
        mkdir -p "$python_home_for_app/lib"
        cp "$PYTHON_FRAMEWORK_BINARY" "$python_home_for_app/Python"
        mkdir -p "$python_home_for_app/Resources"
        cp "$PYTHON_FRAMEWORK_DIR/Versions/3.13/Resources/Info.plist" \
            "$python_home_for_app/Resources/Info.plist"
        ln -s 3.13 "$python_framework/Versions/Current"
        ln -s Versions/Current/Python "$python_framework/Python"
        ln -s Versions/Current/Resources "$python_framework/Resources"
        rsync -a \
            --exclude site-packages \
            --exclude config-3.13-darwin \
            --exclude test \
            --exclude __pycache__ \
            --exclude '*.pyc' \
            "$PYTHON_HOME_DIR/lib/python3.13/" \
            "$python_home_for_app/lib/python3.13/"
        install_name_tool -change \
            "$PYTHON_FRAMEWORK_LOAD_PATH" \
            "@executable_path/../Frameworks/Python.framework/Versions/3.13/Python" \
            "$macos/ConsumerSmoke"
        install_name_tool -change \
            "$PYTHON_FRAMEWORK_LOAD_PATH" \
            "@executable_path/../Frameworks/Python.framework/Versions/3.13/Python" \
            "$macos/SwiftPythonWorker"
        install_name_tool -change \
            "$AUDIO_PROBE_PYTHON_LOAD_PATH" \
            "@executable_path/../Frameworks/Python.framework/Versions/3.13/Python" \
            "$macos/SwiftPythonAudioProbe"
        if otool -L "$embedded_engine_binary" \
            | grep -F "$PYTHON_FRAMEWORK_LOAD_PATH" >/dev/null; then
            install_name_tool -change \
                "$PYTHON_FRAMEWORK_LOAD_PATH" \
                "@executable_path/../Frameworks/Python.framework/Versions/3.13/Python" \
                "$embedded_engine_binary"
        fi

        # Library validation accepts the embedded interpreter and extension
        # modules because every Mach-O is signed by the app's own team.
        while IFS= read -r -d '' nested_binary; do
            if file "$nested_binary" | grep -q 'Mach-O'; then
                codesign --force --sign "$identity" --options runtime \
                    "${CODESIGN_TIMESTAMP_ARGS[@]}" "$nested_binary"
            fi
        done < <(find "$python_home_for_app" -type f -print0)

        # Gatekeeper evaluates executable-bit payloads in quarantined bundles
        # as code. Python installs contain plain-text helper scripts with that
        # bit set, so normalize them before sealing the framework/app.
        while IFS= read -r -d '' executable_payload; do
            if ! file "$executable_payload" | grep -q 'Mach-O'; then
                chmod a-x "$executable_payload"
            fi
        done < <(find "$python_home_for_app" -type f -perm -111 -print0)

        codesign --force --sign "$identity" --options runtime \
            "${CODESIGN_TIMESTAMP_ARGS[@]}" "$python_framework"
    fi
    codesign --force --sign "$identity" --options runtime \
        "${CODESIGN_TIMESTAMP_ARGS[@]}" "$embedded_engine"
    cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>ConsumerSmoke</string>
  <key>CFBundleIdentifier</key><string>$bundle_identifier</string>
  <key>CFBundleName</key><string>ConsumerSmoke</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>SwiftPython verifies bounded duplex audio readiness before starting media.</string>
</dict>
</plist>
EOF
    local worker_identifier="$bundle_identifier.SwiftPythonWorker"
    local probe_identifier="$bundle_identifier.SwiftPythonAudioProbe"
    codesign --force --sign "$identity" --options runtime \
        --identifier "$worker_identifier" \
        "${CODESIGN_TIMESTAMP_ARGS[@]}" --entitlements "$worker_entitlements" \
        "$macos/SwiftPythonWorker"
    codesign --force --sign "$identity" --options runtime \
        --identifier "$probe_identifier" \
        "${CODESIGN_TIMESTAMP_ARGS[@]}" --entitlements "$probe_entitlements" \
        "$macos/SwiftPythonAudioProbe"
    codesign --force --sign "$identity" --options runtime \
        "${CODESIGN_TIMESTAMP_ARGS[@]}" \
        --entitlements "$parent_entitlements" "$app"
    codesign --verify --deep --strict --verbose=2 "$app"
    assert_signed_entitlements_match "$app" "$parent_entitlements"
    assert_signed_entitlements_match \
        "$macos/SwiftPythonAudioProbe" \
        "$probe_entitlements"
    if [ "$mode" = sandbox ]; then
        assert_parent_audio_policy "$app" 1
    else
        assert_parent_audio_policy "$app" 0
    fi
    assert_nested_probe_identity "$app"
    local before_run_digest
    before_run_digest="$(bundle_content_digest "$app")"
    run_with_timeout 90 env \
        SWIFTPYTHON_WORKER_PATH="$macos/SwiftPythonWorker" \
        SWIFTPYTHON_AUDIO_PROBE_GATE="$effective_audio_probe_gate" \
        SWIFTPYTHON_SKIP_IN_PROCESS="$skip_in_process" \
        PYTHONHOME="$python_home_for_app" \
        PYTHONNOUSERSITE=1 \
        PYTHONDONTWRITEBYTECODE=1 \
        "$macos/ConsumerSmoke"
    assert_bundle_remained_sealed "$app"
    assert_bundle_content_unchanged "$app" "$before_run_digest"
}

notarize_app() {
    local mode="$1"
    local app="$WORK_DIR/ConsumerSmoke-$mode.app"
    local archive="$NOTARY_OUTPUT_DIR/ConsumerSmoke-$mode-notary.zip"
    local result="$NOTARY_OUTPUT_DIR/ConsumerSmoke-$mode-notary-result.json"
    local log="$NOTARY_OUTPUT_DIR/ConsumerSmoke-$mode-notary-log.json"
    local assessment="$NOTARY_OUTPUT_DIR/ConsumerSmoke-$mode-spctl.txt"

    mkdir -p "$NOTARY_OUTPUT_DIR"
    if [ -e "$archive" ] || [ -e "$result" ] || [ -e "$log" ] || [ -e "$assessment" ]; then
        echo "Refusing to overwrite notarization evidence for $mode in $NOTARY_OUTPUT_DIR" >&2
        exit 1
    fi

    ditto -c -k --keepParent "$app" "$archive"
    xcrun notarytool submit "$archive" \
        --keychain-profile "$NOTARY_PROFILE" \
        --wait --timeout 45m --output-format json > "$result"

    local status
    local submission_id
    status="$(jq -r '.status' "$result")"
    submission_id="$(jq -r '.id' "$result")"
    if [ "$status" != Accepted ] || [ -z "$submission_id" ] || [ "$submission_id" = null ]; then
        cat "$result" >&2
        echo "Notarization did not return Accepted for $mode" >&2
        exit 1
    fi

    xcrun notarytool log "$submission_id" \
        --keychain-profile "$NOTARY_PROFILE" "$log"
    if [ "$(jq -r '.status' "$log")" != Accepted ] \
        || [ "$(jq -r '.statusCode' "$log")" != 0 ] \
        || [ "$(jq -r '.issues | length' "$log")" != 0 ]; then
        jq '{status,statusSummary,statusCode,issues}' "$log" >&2
        exit 1
    fi

    xcrun stapler staple "$app"
    xcrun stapler validate "$app"
    xattr -w com.apple.quarantine \
        "0083;$(date +%s);SwiftPythonNotaryGate;" "$app"
    if ! spctl --assess --type execute --verbose=4 "$app" \
        > "$assessment" 2>&1; then
        cat "$assessment" >&2
        exit 1
    fi
    if ! grep -F 'source=Notarized Developer ID' "$assessment" >/dev/null; then
        cat "$assessment" >&2
        echo "Gatekeeper did not report Notarized Developer ID for $mode" >&2
        exit 1
    fi

    codesign --verify --deep --strict --verbose=2 "$app"
    echo "Notarized consumer passed: $mode ($submission_id)"
    cat "$assessment"
}

launchservices_pid_for_bundle_id() {
    local bundle_identifier="$1"
    local asn
    local details
    local process_pid
    asn="$(lsappinfo find "bundleID=$bundle_identifier" 2>/dev/null || true)"
    [ -n "$asn" ] || return 1
    details="$(lsappinfo info -only pid "$asn" 2>/dev/null || true)"
    process_pid="$(
        sed -n 's/.*"pid"=\([0-9][0-9]*\).*/\1/p' <<<"$details" \
            | head -1
    )"
    case "$process_pid" in
        ''|*[!0-9]*|0|1) return 1 ;;
    esac
    kill -0 "$process_pid" 2>/dev/null || return 1
    printf '%s\n' "$process_pid"
}

terminate_launchservices_app() {
    local bundle_identifier="$1"
    local open_pid="$2"
    local app_pid=""
    app_pid="$(
        launchservices_pid_for_bundle_id "$bundle_identifier" 2>/dev/null || true
    )"
    if [ -n "$app_pid" ]; then
        kill -TERM "$app_pid" 2>/dev/null || true
    fi
    kill -TERM "$open_pid" 2>/dev/null || true
    sleep 5
    app_pid="$(
        launchservices_pid_for_bundle_id "$bundle_identifier" 2>/dev/null || true
    )"
    if [ -n "$app_pid" ]; then
        kill -KILL "$app_pid" 2>/dev/null || true
    fi
    kill -KILL "$open_pid" 2>/dev/null || true
}

run_app_via_launchservices() {
    local mode="$1"
    local app="$2"
    local bundle_identifier="$3"
    local python_layout="$4"
    local stdout_path="$NOTARY_OUTPUT_DIR/ConsumerSmoke-$mode-launchservices.stdout"
    local stderr_path="$NOTARY_OUTPUT_DIR/ConsumerSmoke-$mode-launchservices.stderr"
    local receipt_path="$NOTARY_OUTPUT_DIR/ConsumerSmoke-$mode-launchservices.receipt"
    local nonce="$mode-$SMOKE_ID_SUFFIX-$(date +%s)-$$"
    local expected_receipt="swiftpython-launch-services-success=$nonce"
    local existing_pid=""
    local observed_pid=""
    local current_pid=""
    local open_pid
    local open_status=0
    local receipt_count
    local deadline
    local -a open_args=(
        /usr/bin/env
        -u SWIFTPYTHON_WORKER_PATH
        -u SWIFTPYTHON_PACKAGE_PATH
        -u SWIFTPYTHON_SKIP_IN_PROCESS
        -u SWIFTPYTHON_USE_BUNDLED_PYTHON_HOME
        -u PYTHONHOME
        -u PYTHONPATH
        /usr/bin/open
        -W
        -n
        -F
        -g
        --stdout "$stdout_path"
        --stderr "$stderr_path"
        --env "SWIFTPYTHON_AUDIO_PROBE_GATE=$AUDIO_PROBE_GATE"
        --env "SWIFTPYTHON_LAUNCH_SERVICES_RECEIPT_NONCE=$nonce"
        --env "PYTHONNOUSERSITE=1"
        --env "PYTHONDONTWRITEBYTECODE=1"
    )

    for evidence_path in "$stdout_path" "$stderr_path" "$receipt_path"; do
        if [ -e "$evidence_path" ]; then
            echo "Refusing to overwrite LaunchServices evidence: $evidence_path" >&2
            exit 1
        fi
    done
    existing_pid="$(
        launchservices_pid_for_bundle_id "$bundle_identifier" 2>/dev/null || true
    )"
    if [ -n "$existing_pid" ]; then
        echo "Refusing LaunchServices gate with an existing exact app instance: bundle=$bundle_identifier pid=$existing_pid" >&2
        exit 1
    fi

    case "$python_layout" in
        external)
            open_args+=(--env "PYTHONHOME=$PYTHON_HOME_DIR")
            ;;
        bundled)
            open_args+=(
                --env "SWIFTPYTHON_SKIP_IN_PROCESS=1"
                --env "SWIFTPYTHON_USE_BUNDLED_PYTHON_HOME=1"
            )
            ;;
        *)
            echo "Unknown LaunchServices Python layout: $python_layout" >&2
            exit 64
            ;;
    esac
    open_args+=("$app")

    : > "$stdout_path"
    : > "$stderr_path"
    "${open_args[@]}" >>"$stdout_path" 2>>"$stderr_path" &
    open_pid=$!
    deadline=$((SECONDS + 90))
    while kill -0 "$open_pid" 2>/dev/null; do
        current_pid="$(
            launchservices_pid_for_bundle_id "$bundle_identifier" 2>/dev/null || true
        )"
        if [ -n "$current_pid" ]; then
            observed_pid="$current_pid"
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            echo "LaunchServices app timed out: bundle=$bundle_identifier app=$app" >&2
            terminate_launchservices_app "$bundle_identifier" "$open_pid"
            wait "$open_pid" 2>/dev/null || true
            cat "$stderr_path" >&2
            exit 1
        fi
        sleep 0.2
    done
    wait "$open_pid" || open_status=$?
    current_pid="$(
        launchservices_pid_for_bundle_id "$bundle_identifier" 2>/dev/null || true
    )"
    if [ -n "$current_pid" ]; then
        echo "LaunchServices returned while the exact app is still running: bundle=$bundle_identifier pid=$current_pid" >&2
        terminate_launchservices_app "$bundle_identifier" "$open_pid"
        cat "$stderr_path" >&2
        exit 1
    fi
    if [ "$open_status" -ne 0 ]; then
        cat "$stderr_path" >&2
        echo "LaunchServices invocation failed: bundle=$bundle_identifier status=$open_status" >&2
        exit 1
    fi
    if [ -z "$observed_pid" ]; then
        cat "$stderr_path" >&2
        echo "LaunchServices never reported a live exact app instance: bundle=$bundle_identifier" >&2
        exit 1
    fi
    receipt_count="$(grep -Fxc -- "$expected_receipt" "$stdout_path" || true)"
    if [ "$receipt_count" != 1 ]; then
        cat "$stdout_path" >&2
        cat "$stderr_path" >&2
        echo "LaunchServices success receipt count is $receipt_count, expected exactly 1" >&2
        exit 1
    fi
    printf '%s\n' "$expected_receipt" > "$receipt_path"
    echo "LaunchServices consumer passed: $mode (pid $observed_pid)"
}

echo "=== Developer ID non-sandbox signed consumer ==="
make_app \
    developer-id \
    "$DEVELOPER_ID" \
    "$REPO_DIR/Entitlements/ConsumerApp.entitlements" \
    "$REPO_DIR/Entitlements/SwiftPythonWorker.entitlements" \
    "$REPO_DIR/Entitlements/SwiftPythonAudioProbe.entitlements"
DEVELOPER_APP="$WORK_DIR/ConsumerSmoke-developer-id.app"
assert_signed_entitlements_match \
    "$DEVELOPER_APP/Contents/MacOS/SwiftPythonWorker" \
    "$REPO_DIR/Entitlements/SwiftPythonWorker.entitlements"
assert_signed_entitlements_match \
    "$DEVELOPER_APP/Contents/MacOS/SwiftPythonAudioProbe" \
    "$REPO_DIR/Entitlements/SwiftPythonAudioProbe.entitlements"
assert_nested_probe_identity "$DEVELOPER_APP"

echo "=== Developer ID sandbox-inherited signed consumer ==="
make_app \
    sandbox \
    "$DEVELOPER_ID" \
    "$PARENT_SANDBOX_ENTITLEMENTS" \
    "$REPO_DIR/Entitlements/SwiftPythonWorker-sandbox.entitlements" \
    "$REPO_DIR/Entitlements/SwiftPythonAudioProbe-sandbox.entitlements"
SANDBOX_APP="$WORK_DIR/ConsumerSmoke-sandbox.app"
assert_signed_entitlements_match \
    "$SANDBOX_APP/Contents/MacOS/SwiftPythonWorker" \
    "$REPO_DIR/Entitlements/SwiftPythonWorker-sandbox.entitlements"
assert_signed_entitlements_match \
    "$SANDBOX_APP/Contents/MacOS/SwiftPythonAudioProbe" \
    "$REPO_DIR/Entitlements/SwiftPythonAudioProbe-sandbox.entitlements"
test "$(
    codesign -dvv "$SANDBOX_APP/Contents/MacOS/SwiftPythonWorker" 2>&1 \
        | sed -n 's/^Identifier=//p'
)" = "$SANDBOX_BUNDLE_IDENTIFIER.SwiftPythonWorker"
assert_nested_probe_identity "$SANDBOX_APP"

if [ "$VM_RELEASE_GATE" = 1 ]; then
    echo "=== Developer ID virtualization signed consumer ==="
    make_app \
        virtualization \
        "$DEVELOPER_ID" \
        "$PARENT_VM_ENTITLEMENTS" \
        "$REPO_DIR/Entitlements/SwiftPythonWorker.entitlements" \
        "$REPO_DIR/Entitlements/SwiftPythonAudioProbe.entitlements"
    VM_APP="$WORK_DIR/ConsumerSmoke-virtualization.app"
    assert_signed_entitlements_match \
        "$VM_APP" \
        "$PARENT_VM_ENTITLEMENTS"
    assert_signed_entitlements_match \
        "$VM_APP/Contents/MacOS/SwiftPythonAudioProbe" \
        "$REPO_DIR/Entitlements/SwiftPythonAudioProbe.entitlements"
    assert_nested_probe_identity "$VM_APP"
fi

if [ -n "$NOTARY_PROFILE" ]; then
    echo "=== Notarized Developer ID non-sandbox consumer ==="
    notarize_app developer-id
    DEVELOPER_NOTARIZED_DIGEST="$(bundle_content_digest "$DEVELOPER_APP")"
    run_app_via_launchservices \
        developer-id \
        "$DEVELOPER_APP" \
        "$DEVELOPER_BUNDLE_IDENTIFIER" \
        external
    assert_bundle_remained_sealed "$DEVELOPER_APP"
    assert_bundle_content_unchanged \
        "$DEVELOPER_APP" \
        "$DEVELOPER_NOTARIZED_DIGEST"

    echo "=== Notarized Developer ID sandbox-inherited consumer ==="
    notarize_app sandbox
    SANDBOX_NOTARIZED_DIGEST="$(bundle_content_digest "$SANDBOX_APP")"
    run_app_via_launchservices \
        sandbox \
        "$SANDBOX_APP" \
        "$SANDBOX_BUNDLE_IDENTIFIER" \
        bundled
    assert_bundle_remained_sealed "$SANDBOX_APP"
    assert_bundle_content_unchanged \
        "$SANDBOX_APP" \
        "$SANDBOX_NOTARIZED_DIGEST"

    if [ "$VM_RELEASE_GATE" = 1 ]; then
        echo "=== Notarized Developer ID virtualization consumer ==="
        notarize_app virtualization
        VM_NOTARIZED_DIGEST="$(bundle_content_digest "$VM_APP")"
        run_with_timeout 900 env \
            SWIFTPYTHON_WORKER_PATH="$VM_APP/Contents/MacOS/SwiftPythonWorker" \
            SWIFTPYTHON_VM_WORKER_DIR="$REPO_DIR/VMWorker" \
            SWIFTPYTHON_VM_BASE_IMAGE="$VM_BASE_IMAGE" \
            SWIFTPYTHON_VM_SNAPSHOT="$VM_SNAPSHOT" \
            SWIFTPYTHON_VM_RESTORE_SECRET="$VM_RESTORE_SECRET" \
            SWIFTPYTHON_VM_CLONE_DIR="$VM_CLONE_DIR" \
            SWIFTPYTHON_VM_ITERATIONS="$VM_ITERATIONS" \
            PYTHONHOME="$PYTHON_HOME_DIR" \
            PYTHONNOUSERSITE=1 \
            PYTHONDONTWRITEBYTECODE=1 \
            "$VM_APP/Contents/MacOS/ConsumerSmoke"
        assert_bundle_remained_sealed "$VM_APP"
        assert_bundle_content_unchanged "$VM_APP" "$VM_NOTARIZED_DIGEST"
    fi
fi
