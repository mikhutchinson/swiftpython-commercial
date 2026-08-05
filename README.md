# SwiftPython Commercial Distribution

Binary distribution of SwiftPython for macOS applications that need in-process
Python, isolated worker processes, long-lived full-duplex sessions, or
Virtualization.framework-backed Linux tenants.

Current release: `0.6.0-duplex.5`

Product page: [Best Byte AI](https://bestbyteai.com/)

## License

Use this package under one of:

- AGPL-3.0;
- the free Small Organization Commercial Grant in [LICENSE](LICENSE); or
- a written commercial license.

Read [LICENSE](LICENSE) before distributing an application.

## Release contents

| Path or release asset | Purpose |
|---|---|
| `SwiftPythonRuntime.xcframework` | Library-evolved consumer API surface |
| `SwiftPythonEngine.xcframework` | Private code-only dependency; no product or textual Swift module |
| `SwiftPythonAudioInterop.xcframework` | Optional AVAudio capture/playback adapter |
| `SwiftPythonMetalInterop.xcframework` | Optional Metal leases, shared-arena mapping, and copy ledger |
| `SwiftPythonWorker` | Matched arm64 local ProcessPool sidecar |
| `VMWorker/` | Matched five-file generated protocol/helper/supervisor/worker set |
| `Entitlements/` | Parent, worker, inherited-sandbox worker, and virtualization templates |
| `Examples/` | Standalone packages compiled against this public distribution |
| `docs/api-guide/` | Public API and deployment guide |
| `manifest.json` release asset | Version, source revision, protocols, byte sizes, and SHA-256 records |

The four XCFrameworks are universal macOS binaries. The prebuilt
`SwiftPythonWorker` sidecar is arm64; an Intel ProcessPool deployment needs a
same-source x86_64 worker. Keep every binary, helper, image, and snapshot on one
release version. Worker wire v6 is not compatible with the published v0.5
worker wire v5.

The complete `SwiftPythonCommercial-0.6.0-duplex.5.zip` asset contains this
public checkout. The four XCFramework zips are individual binary-target
assets. `manifest.json` is a separate asset and attests all four zips, the
worker, all five VM helpers, the complete distribution, and the same-version VM
image used for the certified VM gate.

## Requirements

- macOS 15 or newer
- Swift 6 / Xcode command-line tools
- Python 3.13 and development libraries
- Apple Silicon for the shipped ProcessPool sidecar
- Virtualization.framework entitlement and Apple Silicon for VM/Sandbox use

The package linker settings discover Homebrew Python 3.13 on Apple Silicon or
Intel. Set `SWIFTPYTHON_PYTHON_LIB_DIR`, `PYTHON_HOME`, or `PYTHONHOME`
for a custom layout. A distributable sandboxed app must bundle Python; Finder
and Dock launches do not inherit shell environment variables.

## Swift Package Manager

Pin the prerelease exactly:

```swift
// Package.swift
dependencies: [
    .package(
        url: "https://github.com/mikhutchinson/swiftpython-commercial.git",
        exact: "0.6.0-duplex.5"
    )
]
```

Choose only the products the application uses:

```swift
.product(
    name: "SwiftPythonRuntime",
    package: "swiftpython-commercial"
)
.product(
    name: "SwiftPythonAudioInterop",
    package: "swiftpython-commercial"
)
.product(
    name: "SwiftPythonMetalInterop",
    package: "swiftpython-commercial"
)
```

`SwiftPythonAudioInterop` and `SwiftPythonMetalInterop` are independent
optional products. A core-only consumer does not link AVFAudio or Metal through
those adapters. `SwiftPythonEngine` is intentionally not a product and must not
be imported. SwiftPM links it as a private dependency of each public product.

## In-process and ProcessPool smoke

```swift
import SwiftPythonRuntime

let version: String = try await Python.run {
    try String(pythonObject: Python.sys.version)
}

try await withProcessPool(workers: 2) { pool in
    let value: Double = try await pool.invokeResult(
        module: "math",
        function: "sqrt",
        args: [.python(144.0)]
    )
    print(version, value)
}
```

For an app bundle, copy `SwiftPythonWorker` into
`Contents/MacOS/SwiftPythonWorker`, then sign nested code before the outer
app. Worker discovery also accepts `SWIFTPYTHON_WORKER_PATH` or the explicit
`workerExecutablePath:` initializer argument.

## App Bundle Layout

```text
YourApp.app/
  Contents/
    Frameworks/
      SwiftPythonEngine.framework/ # required private runtime code, embed once
      Python.framework/        # required for self-contained/sandboxed apps
    MacOS/
      YourApp
      SwiftPythonWorker
    Info.plist
```

Embed the supplied signed `SwiftPythonEngine.framework` exactly once. The
application executable must resolve its `@rpath` install name through
`@executable_path/../Frameworks`; do not copy Engine into multiple nested
locations. The worker, Engine, and host must load the same bundled Python when the app cannot rely
on Homebrew. Copying a sidecar is not a signing or notarization step; use the
distribution-specific rules below.

## Full-duplex sessions

`PythonDuplexSession` pins one worker ID and generation while input, output,
semantic control, and interruption progress independently. It never migrates or
replays after worker replacement.

```swift
let session = try await pool.openDuplexSession(
    handler: .eval(
        code: """
        from swift_duplex import InputFrame
        def run(session):
            session.ready()
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

do {
    try await session.input.send(
        DuplexInputFrame(payload: Data("hello".utf8))
    )
    try await session.input.finish()
    for try await frame in session.output {
        consume(frame.buffer)
        try await session.acknowledgeOutput(
            consumedThrough: DuplexPosition(
                sequence: frame.position.sequence,
                byteOffset: frame.buffer.count
            )
        )
    }
    _ = try await session.result()
    await session.close()
} catch {
    await session.cancel(reason: .user)
    await session.close()
    throw error
}
```

Frame-only sessions require `.frames`. Bounded logical-message fragmentation
requires `.messages`. The local fixed-pool shared-ingress route requires
`.managedBuffers` and a `ManagedBufferConfiguration`; isolated providers may use
inline logical-message chunks instead. Backing paths, offsets, slot topology,
generational counters, and quarantine state are private.
Capability requirements are rechecked atomically against the exact generation
reserved for open.

See [Chapter 10](docs/api-guide/ch10-full-duplex.md) and the runnable
[DuplexSession example](Examples/DuplexSession/).

## VM and Sandbox

`0.6.0-duplex.5` includes the isolated Sandbox surface. Its certified gate uses
the same source revision for:

- all four XCFrameworks and the local sidecar;
- `_swiftpython_wire.py`, `_swiftpython_duplex.py`,
  `swiftpython_protocol.py`, `swiftpython_supervisor.py`, and
  `swiftpython_worker.py`;
- the attested Ubuntu base image and warm snapshot;
- cold and warm vsock duplex/message workloads.

The release manifest identifies the verified image and records the exact guest
helper hashes. Image/snapshot verification must reject a hash, version,
protocol, supervisor, configuration, or restore-secret mismatch; a warm gate
must not silently fall back to cold boot.

Deploy the five-file `VMWorker/` directory together and point custom layouts
at it with:

```bash
SWIFTPYTHON_VM_WORKER_DIR=/absolute/path/to/VMWorker
```

See [Chapter 9](docs/api-guide/ch9-sandbox-vm.md) for image, snapshot, tenant,
shell/PTY, and VM ProcessPool usage.

## Audio and Metal

```swift
import SwiftPythonAudioInterop
import SwiftPythonMetalInterop
```

`DuplexAudioFormat` validates PCM shape before capture/playback.
`DuplexAudioCapture` and `DuplexAudioPlayback` keep AVAudio callbacks
realtime-only; async pumps own session interaction.

`ManagedBuffer.makeMetalBufferLease` maps a page-aligned managed buffer to an
`MTLBuffer` without copying those pages. Registered command buffers and access
completion hold the opaque allocation until safe reuse.
`DuplexCopyLedger` records actual zero-copy, bounded CPU-copy, or kernel-copy
routes. It does not turn capture-source, IOSurface, socket, or VM routes into a
blanket zero-copy claim.

See [Chapter 11](docs/api-guide/ch11-apple-interop.md).

## App Sandbox and signing

Select worker entitlements from the parent app's sandbox state, not from the
certificate class:

| Parent | Worker template |
|---|---|
| Non-sandbox, any signing identity | `SwiftPythonWorker.entitlements` |
| Sandboxed, Apple Development or Developer ID | `SwiftPythonWorker-sandbox.entitlements` |

The inherited worker template contains exactly
`com.apple.security.app-sandbox` and `com.apple.security.inherit`.
Capabilities belong on the parent. For a sandboxed distribution, bundle Python,
rewrite both host and worker load commands to the bundle-local framework,
same-team-sign native code, and give the helper an identifier nested beneath
the parent bundle identifier.

```bash
codesign --force --sign "$SIGN_ID" --options runtime \
  --entitlements Entitlements/SwiftPythonWorker.entitlements \
  YourApp.app/Contents/MacOS/SwiftPythonWorker

codesign --force --sign "$SIGN_ID" --options runtime \
  --entitlements Entitlements/ConsumerApp.entitlements \
  YourApp.app
```

The release gate notarizes the exact complete distribution and three app-shaped
consumer fixtures (non-sandbox, inherited sandbox, and virtualization), staples
them, applies quarantine provenance, requires
`source=Notarized Developer ID`, reruns duplex, and rechecks each signed bundle
for mutation. The virtualization fixture must complete 20 consecutive positive warm restores
and the full public VM tenant workload without accepting a cold fallback.
Before publication, `scripts/consumer_path_smoke.sh` derives a temporary
path-based binary manifest from this checkout. That keeps every local and
notarized fixture on the candidate XCFramework bytes instead of resolving the
preceding hosted tag or requiring the new asset URLs to exist early.

## Build and run public evidence

```bash
swift build
swift test
swift run swiftpython-smoke
swift run --package-path Examples/CoreRuntimeSmoke
swift run --package-path Examples/ProcessPoolSmoke
swift run --package-path Examples/BridgingRing
swift run -c release --package-path Examples/SharedTensorPipeline
swift run --package-path Examples/DuplexSession
scripts/audit_release_surface.sh 0.6.0-duplex.5
scripts/consumer_path_smoke.sh
```

For the final notarized VM release gate, supply the same-commit image and
snapshot explicitly:

```bash
SWIFTPYTHON_NOTARY_PROFILE="<notarytool-keychain-profile>" \
SWIFTPYTHON_NOTARY_OUTPUT_DIR="$PWD/notarization" \
SWIFTPYTHON_VM_RELEASE_GATE=1 \
SWIFTPYTHON_VM_BASE_IMAGE=/absolute/path/to/base-ubuntu.img \
SWIFTPYTHON_VM_SNAPSHOT=/absolute/path/to/snapshot \
SWIFTPYTHON_VM_RESTORE_SECRET=/absolute/path/to/snapshot.restore-secret \
SWIFTPYTHON_VM_CLONE_DIR=/absolute/path/to/consumer-clones \
SWIFTPYTHON_VM_ITERATIONS=20 \
  scripts/consumer_path_smoke.sh
```

Every standalone example defaults to this checkout. For a published-tag proof,
set `SWIFTPYTHON_COMMERCIAL_PACKAGE_URL` and
`SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION`; prerelease identifiers are
preserved rather than collapsed to `0.6.0`.

## Troubleshooting

| Symptom | Check |
|---|---|
| `Library not loaded: libpython3.13.dylib` | Bundle/select the same Python 3.13 layout used at link time |
| `workerNotFound` | Copy the matched sidecar or set its explicit path |
| protocol/helper/media skew | Compare the release tag and `manifest.json`; never mix helpers |
| `Bad CPU type` for the worker | The shipped sidecar is arm64; build a matched x86_64 worker for Intel |
| duplex `featureUnavailable` | Inspect live capabilities and put requirements on the open |
| arena requirement rejected in VM | Expected: shared arena ingress is local UDS only |
| VM image/snapshot rejected | Rebuild all five helpers, image, and snapshot from this release |
| SPM fingerprint mismatch | Do not reuse tags; clear stale local resolution state and resolve the new version |

## Release notes

### 0.6.0-duplex.5

- Added opaque, generation-bound application-control receipts with repeatable
  owned, rejected, pending, and delivery-uncertain resolution. A timeout or
  cancellation no longer destroys ownership knowledge or permits replay.
- Preserved late control acknowledgement across terminal cleanup and replaced
  a worker after uncertain partial control-frame transport failure.
- Aligned native duplex Python timeout and argument exception types with the VM
  worker, while preserving arbitrary accelerator callable failures.
- Hardened complete-distribution manifest ordering, candidate-binary consumer
  gates, and interactive QEMU image construction.

### 0.6.0-duplex.4

- Added equality-only worker lifetime tokens for generation-safe consumer state
  without exposing generation counters.
- Added provider-neutral exact sandbox policy, sanitized lifecycle and failure
  diagnostics, explicit activity-stream loss, and confirmed termination.
- Preserved the code-only private Engine boundary introduced in `.3`.

### 0.6.0-duplex.3

- Split proprietary transport, managed-memory, sandbox, and tuning code into
  the private code-only `SwiftPythonEngine` framework.
- Removed Engine Swift module metadata and textual interfaces from the
  distribution; the public Runtime interface contains only consumer APIs.
- Replaced low-level arenas, leases, ring buffers, VM builders/configuration,
  and policy thresholds with managed handles, presets, and `SandboxProvider`.
- Added private-Engine signing, embedding, one-copy load, interface-denylist,
  and consumer behavior gates.

### 0.6.0-duplex.2

- Worker wire v6 and first-class `PythonDuplexSession` with bounded credit,
  half-close, control, interruption, terminal watermarks, and no replay.
- Feature-negotiated logical messages with bounded fragmentation and
  reassembly, plus local owned fixed-pool shared-arena ingress.
- Separate optional Audio and Metal XCFramework products with realtime adapter
  contracts, lease-safe GPU completion, poison/quarantine semantics, and a
  route-specific copy ledger.
- Same-version five-helper VM image/snapshot line with authenticated vsock
  duplex and cold/warm restore gates.
- Dual SwiftPM/xcodebuild module layouts, strengthened external consumer,
  sandbox inheritance, notarization, Gatekeeper, and hosted-byte verification.

Prior release history is recorded in the source repository changelog and older
commercial tags.
