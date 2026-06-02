# SwiftPython Runtime Binary Distribution

Public binary distribution for the SwiftPython runtime on macOS.

This repository is intentionally public so SwiftPython users can depend on it
with Swift Package Manager. SwiftPython is available under three paths:
AGPL-3.0 for AGPL-compliant open source applications, a free Small Organization
Commercial Grant for individuals and organizations with fewer than 10 total
employees and contractors, or a written commercial license for larger
proprietary/closed-source use. The SwiftPython source code, generator pipeline,
and implementation internals remain private. You do not need the private
repository to build an app against this package.

Current release: `0.5.6`

Product page: [Best Byte AI](https://bestbyteai.com/)

## Licensing at a Glance

| Use case | Path |
|----------|------|
| AGPL-compliant open source app or service | AGPL-3.0 |
| Individual or organization under 10 total employees/contractors | Free Small Organization Commercial Grant |
| Organization with 10 or more total employees/contractors | Written SwiftPython Commercial license |

The small-organization threshold counts parent companies, subsidiaries, and
controlled affiliates. This table is only a summary; see [LICENSE](LICENSE)
for the exact terms.

## What Ships

| Artifact | Purpose |
|----------|---------|
| `SwiftPythonRuntime.xcframework` | Swift runtime API and binary library |
| `SwiftPythonWorker` | Local worker sidecar for `PythonProcessPool` |
| `VMWorker/` | Python supervisor and worker scripts for VM tenants |
| `Entitlements/` | Hardened runtime, sandbox, and VM entitlement templates |
| `Examples/IrisDemo/` | Complete macOS IRIS app built against the public runtime |
| `Examples/CoreRuntimeSmoke/` | Minimal CLI: in-process `Python.run` |
| `Examples/ProcessPoolSmoke/` | Minimal CLI: `withProcessPool` + `invokeResult` |
| `Examples/BridgingRing/` | Pool IPC tour: Swift↔Python callbacks, `WorkerCallbackContext`, `evalEvents` |
| `Examples/SharedTensorPipeline/` | Headline demo: shared-memory arena + out-of-band streaming + concurrent IPC |
| `docs/api-guide/` | Public API guide and integration recipes |
| `scripts/consumer_path_smoke.sh` | Verifies a separate Swift package can consume this checkout by local path |

Keep the XCFramework, worker binary, and `VMWorker/` scripts on the same tag.
Do not mix artifacts from different releases.

## Distribution Modes

Use Swift Package Manager, a tagged source archive, or a complete
`SwiftPythonCommercial-<version>.zip` release asset when available. Those
include the package manifest, worker sidecar, `VMWorker/` scripts, entitlements,
examples, and docs.

`SwiftPythonRuntime.xcframework.zip` is the runtime binary artifact only. It is
not a complete installable distribution by itself for `PythonProcessPool`,
SandboxPool, or the examples because those also need the matched worker and
support files from the same release tag.

## Requirements

- macOS 15.0+
- Swift 6.0+
- Python 3.13, Homebrew recommended for development
- Xcode command line tools
- Apple Silicon Mac for the shipped `SwiftPythonWorker` sidecar and VM/SandboxPool path

For Python 3.13 via Homebrew:

```bash
brew install python@3.13
```

The package and examples auto-detect Homebrew's Apple Silicon and Intel
prefixes. For custom Python layouts, set `SWIFTPYTHON_PYTHON_LIB_DIR`,
`PYTHON_HOME`, or `PYTHONHOME` before building.

## Add the Package

```swift
// Package.swift
dependencies: [
    .package(
        url: "https://github.com/mikhutchinson/swiftpython-commercial.git",
        from: "0.5.6"
    )
]
```

```swift
.target(
    name: "YourAppCore",
    dependencies: [
        .product(name: "SwiftPythonRuntime", package: "swiftpython-commercial")
    ],
    linkerSettings: [
        .unsafeFlags([
            "-L/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib",
            "-lpython3.13"
        ])
    ]
)
```

Import the runtime:

```swift
import SwiftPythonRuntime
```

## Smoke Test

```swift
let version: String = try await Python.run {
    try String(pythonObject: Python.sys.version)
}

print(version)
```

For worker execution:

```swift
try await withProcessPool(workers: 2) { pool in
    let value: Double = try await pool.invokeResult(
        module: "math",
        function: "sqrt",
        args: [.python(144.0)]
    )
    print(value)
}
```

## Public API Guide

Start here: [docs/api-guide](docs/api-guide/).

For runnable samples, see [Examples](Examples/) (including [IrisDemo](Examples/IrisDemo/)).
The examples resolve this checkout by local path by default; no package URL or
version environment variables are needed when running them from a clone.

First-run smoke from a local checkout:

```bash
swift build
swift test
swift run swiftpython-smoke
swift run --package-path Examples/CoreRuntimeSmoke
swift run --package-path Examples/ProcessPoolSmoke
swift run --package-path Examples/BridgingRing
swift run -c release --package-path Examples/SharedTensorPipeline
scripts/consumer_path_smoke.sh
```

The root package includes a small `swiftpython-smoke` executable only to make
first contact obvious. The richer examples live under `Examples/`.
`scripts/consumer_path_smoke.sh` creates a temporary external Swift package,
depends on this checkout by path, and verifies both `Python.run` and
`withProcessPool`.

## How It Compares to Other Swift ↔ Python Options

Most existing Swift ↔ Python bridges put CPython **in your app's address
space**: one interpreter, one GIL, one crash domain. SwiftPython runs the
same `Python.run` model in-process *and* a multi-process `PythonProcessPool`
with a documented IPC protocol on top.

| Axis | In-process bridge (e.g. PythonKit) | SwiftPython |
|------|-----------------------------------|-------------|
| Process model | Embeds `libpython` in your app | In-process `Python.run` **plus** `PythonProcessPool` worker processes |
| Parallelism | Limited by a single GIL | One CPython + one GIL per worker; multiple workers run in parallel |
| Crash isolation | Python crash takes down the host | Worker crashes are surfaced as Swift errors; respawn is built-in |
| Native extensions (NumPy, Torch, …) | Yes, in the host process | Yes — in workers, where a crashing extension does not kill your app |
| Shared-memory tensors across processes | N/A | `pool.createSharedTensor` + `withSharedBuffer` (typed Swift access into POSIX shm) |
| Out-of-band streaming | N/A | `SharedRingBuffer` (POSIX shm) **or** `SocketOOBStreamBuffer` (UDS / vsock) — stream without holding the worker IPC socket, process-pool or VM tenant |
| Python → Swift callbacks | N/A | `registerCallback`, `registerReentrantCallback`, `registerStreamingCallback`, `registerAsyncCallback` |
| VM-isolated tenants | N/A | `SandboxPool` with Linux VM guests (`execShell`, `execShellPTY`) |

This is a positioning summary, not a claim of feature parity in either
direction; PythonKit-style in-process bridges remain a perfectly good fit when
your workload is small, trusted, and never needs to leave the app process.
SwiftPython is built for the cases where you want isolation, parallelism, or
large shared buffers in addition to the in-process surface.

For a single executable that exercises the shared-memory arena, out-of-band
streaming, and concurrent IPC together, see
[`Examples/SharedTensorPipeline`](Examples/SharedTensorPipeline/).

The guide covers:

- in-process `Python.run`,
- Swift/Python conversion and buffers,
- `PyHandle` and `OwnedPyHandle`,
- `PythonProcessPool`,
- streaming values and progress,
- DAG orchestration,
- Python-to-Swift callbacks,
- app-level wrappers for Python packages,
- VM tenants and shell/PTY exec through `SandboxPool`.

## App Bundle Layout

For `.app` bundles, copy `SwiftPythonWorker` into `Contents/MacOS` next to your
main executable.

```text
YourApp.app/
  Contents/
    MacOS/
      YourApp
      SwiftPythonWorker
    Info.plist
```

If your app uses VM tenants and cannot rely on the SPM checkout at runtime,
deploy `VMWorker/` with your app and set:

```bash
SWIFTPYTHON_VM_WORKER_DIR=/absolute/path/to/VMWorker
```

For development tools and CLIs, you can point directly at the worker:

```bash
SWIFTPYTHON_WORKER_PATH=/absolute/path/to/SwiftPythonWorker
```

or pass `workerExecutablePath:` when creating `PythonProcessPool`.

## Finder and Dock Launches

Finder-launched apps do not inherit your shell environment. If using Homebrew
Python, set `PYTHONHOME` and `PATH` before starting your real app binary.

Example wrapper:

```bash
#!/bin/bash
export PYTHONHOME="/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13"
export PATH="$PYTHONHOME/bin:$PATH"
exec "$(dirname "$0")/YourApp.bin"
```

Apps that bundle Python should set these paths to their bundled framework or
virtual environment instead.

## Code Signing and Entitlements

Python native extensions often require hardened runtime exceptions for dynamic
library loading and executable memory. The worker is a separate executable, so
it must be signed separately from your app.

Sign the worker after copying it into the app:

```bash
codesign --force --sign "$SIGN_ID" --options runtime \
  --entitlements Entitlements/SwiftPythonWorker.entitlements \
  "YourApp.app/Contents/MacOS/SwiftPythonWorker"
```

Sign the app with the consumer entitlement template:

```bash
codesign --force --sign "$SIGN_ID" --options runtime \
  --entitlements Entitlements/ConsumerApp.entitlements \
  "YourApp.app/Contents/MacOS/YourApp"
```

Sign inner binaries first, then the outer app.

During development, an Apple Development signing identity avoids repeated
keychain prompts that happen with ad-hoc signing:

```bash
IDENTITY=$(security find-identity -v -p codesigning | grep "Apple Development" | head -1 | awk -F'"' '{print $2}')
SIGN_ID="${IDENTITY:--}"
```

### App Sandbox

Use `Entitlements/SwiftPythonWorker-sandbox.entitlements` only when the parent
app is sandboxed. It includes sandbox inheritance for the worker.

Do not sign a worker with sandbox inheritance if the parent app is not sandboxed;
the worker will get a restrictive default sandbox and may be unable to load
Python packages.

### VM Entitlement

VM-backed features require Apple's Virtualization.framework entitlement. Use the
provided VM entitlement template as the starting point for tools that build or
boot VM tenants.

## SandboxPool Quick Start

Build or locate a prepared Ubuntu image, then create a pool:

```swift
let builder = UbuntuImageBuilder(
    outputDir: "/Users/me/Library/Application Support/MyApp/Images",
    swiftpythonVersion: "0.5.6"
)
let image = try await builder.build()

let sandbox = try await SandboxPool(
    baseImagePath: image,
    cloneDir: "/Users/me/Library/Application Support/MyApp/Sandboxes"
)

let tenantID = SandboxTenantID(rawValue: "default")
let tenant = try await sandbox.acquire(tenantID: tenantID)
let result = try await sandbox.execShell(
    tenantID: tenant.id,
    "python3 --version"
)

print(String(decoding: result.stdout, as: UTF8.self))
try await sandbox.release(tenant)
await sandbox.shutdown()
```

See [Sandbox and VM Exec](docs/api-guide/ch9-sandbox-vm.md) for tenant
lifetime, shell streaming, PTY sessions, events, and VM configuration.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Library not loaded: libpython3.13.dylib` | Set `PYTHONHOME`, `PATH`, and linker flags for the Python 3.13 runtime you ship |
| `workerNotFound(searchedPaths:)` | Copy `SwiftPythonWorker` into the app or pass `workerExecutablePath:` |
| `protocolError` mentioning protocol v5 | Runtime, worker, and VM scripts are not from the same release tag |
| `Bad CPU type in executable` launching `SwiftPythonWorker` | Use Apple Silicon for the shipped sidecar, or build and ship your own matching worker for Intel |
| Worker starts in Terminal but not Finder | Your app launch environment is missing Python paths |
| Python package imports in app but not worker | Worker process sees a different Python environment; set app launch environment consistently |
| `VMWorker scripts not found` | Deploy `VMWorker/` or set `SWIFTPYTHON_VM_WORKER_DIR` |
| SPM fingerprint mismatch | Delete `.build/`, `Package.resolved`, and SwiftPM fingerprint cache, then resolve again |

## Release Notes

| Version | Notes |
|---------|-------|
| 0.5.6 | Shutdown-safe stream recovery: active value/event streams and manual respawn skip worker repair after pool drain or shutdown |
| 0.5.5 | Typed ProcessPool callback overloads through four arguments across sync, async, and reentrant APIs |
| 0.5.4 | Oversized-payload channel recovery: send-side payload caps and parent-side frame draining keep IPC channels usable after typed payload-too-large failures |
| 0.5.3 | Public distribution polish: local-clone example resolution, first-run smoke target, consumer path dependency smoke, free Small Organization Commercial Grant, full distribution release zip, cleaned XCFramework metadata, and matched worker discovery from SwiftPM workspaces |
| 0.5.2 | Runtime reliability patch: callback registration ownership, bounded stream demux backpressure, OOB writer failure signaling, VM health ping timeouts, SandboxPool active tenant locks, and release artifact checksum/upload generation |
| 0.5.1 | ProcessPool reliability patch: CI-exercised reentrant callback fast-fail, bounded per-stream timeout cleanup, typed oversized-command errors, and failed-init worker cleanup |
| 0.5.0 | ProcessPool async callbacks: `registerAsyncCallback`, worker `swift_bridge.call_async`, protocol-v5 callback IPC, and matched VM/Sandbox worker parity |
| 0.4.0 | SandboxPool and VM supervisor runtime: Ubuntu image builder, VM tenant pool, shell capture/stream/PTY, quota and policy controls, packaged `VMWorker/` scripts |
| 0.3.0 | Multi-stream worker protocol and public streaming surface cleanup |
| 0.2.1 | Public worker respawn API with force-kill path |
| 0.2.0 | Streaming overhaul: keepalive, progress events, owned handles, pool events, callback orphan observability |
| 0.1.x | Initial commercial binary releases, worker hardening, app bundle fixes, shared buffer support |

## License

License: AGPL-3.0, free Small Organization Commercial Grant, or written
commercial license. See [LICENSE](LICENSE).
