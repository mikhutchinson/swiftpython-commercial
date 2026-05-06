# SwiftPython Commercial Runtime

Public binary distribution for the SwiftPython commercial runtime on macOS.

This repository is intentionally public so licensed consumers can depend on it
with Swift Package Manager. The SwiftPython source code, generator pipeline, and
implementation internals remain private. You do not need the private repository
to build an app against this package.

Current release: `0.5.0`

## What Ships

| Artifact | Purpose |
|----------|---------|
| `SwiftPythonRuntime.xcframework` | Swift runtime API and binary library |
| `SwiftPythonWorker` | Local worker sidecar for `PythonProcessPool` |
| `VMWorker/` | Python supervisor and worker scripts for VM tenants |
| `Entitlements/` | Hardened runtime, sandbox, and VM entitlement templates |
| `docs/api-guide/` | Public API guide and integration recipes |

Keep the XCFramework, worker binary, and `VMWorker/` scripts on the same tag.
Do not mix artifacts from different releases.

## Requirements

- macOS 15.0+
- Swift 6.0+
- Python 3.13, Homebrew recommended for development
- Xcode command line tools
- Apple Silicon Mac for the VM/SandboxPool path

For Python 3.13 via Homebrew:

```bash
brew install python@3.13
```

## Add the Package

```swift
// Package.swift
dependencies: [
    .package(
        url: "https://github.com/mikhutchinson/swiftpython-commercial.git",
        from: "0.5.0"
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
    swiftpythonVersion: "0.5.0"
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
| Worker starts in Terminal but not Finder | Your app launch environment is missing Python paths |
| Python package imports in app but not worker | Worker process sees a different Python environment; set app launch environment consistently |
| `VMWorker scripts not found` | Deploy `VMWorker/` or set `SWIFTPYTHON_VM_WORKER_DIR` |
| SPM fingerprint mismatch | Delete `.build/`, `Package.resolved`, and SwiftPM fingerprint cache, then resolve again |

## Release Notes

| Version | Notes |
|---------|-------|
| 0.5.0 | ProcessPool async callbacks: `registerAsyncCallback`, worker `swift_bridge.call_async`, protocol-v5 callback IPC, and matched VM/Sandbox worker parity |
| 0.4.0 | SandboxPool and VM supervisor runtime: Ubuntu image builder, VM tenant pool, shell capture/stream/PTY, quota and policy controls, packaged `VMWorker/` scripts |
| 0.3.0 | Multi-stream worker protocol and public streaming surface cleanup |
| 0.2.1 | Public worker respawn API with force-kill path |
| 0.2.0 | Streaming overhaul: keepalive, progress events, owned handles, pool events, callback orphan observability |
| 0.1.x | Initial commercial binary releases, worker hardening, app bundle fixes, shared buffer support |

## License

Commercial license. See [LICENSE](LICENSE).
