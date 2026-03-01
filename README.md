# SwiftPython Commercial Runtime

Binary distribution of the SwiftPython runtime for macOS. This package provides a pre-built XCFramework and worker binary for consuming SwiftPython functionality via Swift Package Manager.

## Requirements

- macOS 15.0+
- Swift 6.0+
- Python 3.13 (Homebrew recommended)

## Installation

Add this package to your `Package.swift` dependencies:

```swift
dependencies: [
    .package(url: "https://github.com/mikhutchinson/swiftpython-commercial.git", from: "0.1.8")
]
```

Or use environment variables for dynamic resolution:

```bash
export SWIFTPYTHON_COMMERCIAL_PACKAGE_URL=https://github.com/mikhutchinson/swiftpython-commercial.git
export SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION=0.1.8
```

## Usage

The package provides two binary artifacts:

- `SwiftPythonRuntime.xcframework` — The runtime library (link against this target)
- `SwiftPythonWorker` — Sidecar process for Python execution

Your app bundle should include the `SwiftPythonWorker` binary alongside your main executable.

## Linker Configuration

The XCFramework links against Python 3.13. Ensure your `Package.swift` includes the appropriate linker flags:

```swift
linkerSettings: [
    .unsafeFlags([
        "-L/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib",
        "-lpython3.13"
    ])
]
```

## App Bundle Structure

When building a `.app` bundle, place resource bundles at the app root and include the worker in `Contents/MacOS/`:

```
YourApp.app/
├── Contents/
│   ├── MacOS/
│   │   ├── YourApp              ← main binary
│   │   └── SwiftPythonWorker    ← sidecar (required)
│   └── Info.plist
└── [SPM resource bundles]
```

A wrapper launcher script is recommended to set `PYTHONHOME` for Finder/Dock launches:

```bash
#!/bin/bash
export PYTHONHOME="/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13"
export PATH="$PYTHONHOME/bin:$PATH"
exec "$(dirname "$0")/YourApp.bin"
```

## Entitlements & Code Signing

The `SwiftPythonWorker` and your consumer app both need specific entitlements to work under macOS [Hardened Runtime](https://developer.apple.com/documentation/security/hardened-runtime) (required for notarization) and optionally [App Sandbox](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app).

### Why entitlements are needed

Python loads ad-hoc signed `.so` extension modules (NumPy, Torch, etc.) and uses `mmap(PROT_WRITE|PROT_EXEC)` internally. Under Hardened Runtime, macOS blocks both behaviors unless the executable carries the appropriate code signing exceptions. Since the worker is a separate process (spawned via `posix_spawn`), it needs its own entitlements — it does not inherit from your app.

### Worker (pre-signed)

The `SwiftPythonWorker` ships pre-signed with hardened runtime, an embedded `CFBundleIdentifier`, and 3 code signing exceptions:

| Entitlement | Purpose |
|-------------|---------|
| `cs.allow-unsigned-executable-memory` | Python/NumPy/SciPy use `mmap(PROT_EXEC)` without `MAP_JIT` |
| `cs.disable-library-validation` | Load ad-hoc signed `.so` modules and Homebrew's `libpython` |
| `cs.allow-dyld-environment-variables` | Safety net for non-Homebrew Python installs using `DYLD_LIBRARY_PATH` |

**After copying the worker into your `.app` bundle, you must re-sign it** — copying invalidates the signature:

```bash
codesign --force --sign - --options runtime \
  --entitlements Entitlements/SwiftPythonWorker.entitlements \
  "YourApp.app/Contents/MacOS/SwiftPythonWorker"
```

### Consumer app

Your app binary links `libpython3.13` (via the XCFramework static lib) and needs its own entitlements. Use the `Entitlements/ConsumerApp.entitlements` template:

```bash
codesign --force --sign - --options runtime \
  --entitlements Entitlements/ConsumerApp.entitlements \
  "YourApp.app/Contents/MacOS/YourApp"
```

For notarization, replace `--sign -` with `--sign "Developer ID Application: Your Name (TEAMID)"`. **Sign innermost first** (worker, then app).

### App Sandbox (Mac App Store)

A separate `SwiftPythonWorker-sandbox.entitlements` is provided for Mac App Store distribution where the parent app is sandboxed. It adds `app-sandbox` + `inherit` so the worker inherits the parent's sandbox profile.

> **Warning**: Do NOT use the sandbox entitlements if the parent app is not sandboxed. `app-sandbox: true` always activates sandboxing — when the parent isn't sandboxed, the worker gets a default restrictive sandbox that blocks Python file access (`/opt/homebrew/`, site-packages, etc.), causing it to hang on initialization.

To use:
1. Sign the worker with `SwiftPythonWorker-sandbox.entitlements` instead of `SwiftPythonWorker.entitlements`
2. Enable App Sandbox on your consumer app (uncomment sandbox keys in `ConsumerApp.entitlements`)
3. Ensure Python framework + site-packages are accessible within the sandbox (bundled in the app or in a sandbox-readable location)

> **Caveat**: `com.apple.security.get-task-allow` (injected by Xcode in debug builds) is incompatible with `com.apple.security.inherit`. If the worker crashes on launch during development, ensure "Code Sign On Copy" is checked in your Xcode build phase, or set `CODE_SIGN_INJECT_BASE_ENTITLEMENTS = NO`.

## Troubleshooting

| Issue | Resolution |
|-------|------------|
| `Library not loaded: libpython3.13.dylib` | Ensure `PYTHONHOME` and `DYLD_LIBRARY_PATH` are set correctly |
| `compiled module was created by a different version of the compiler` | Rebuild your project with the same Swift version used to build this XCFramework |
| SPM fingerprint mismatch | Delete `~/Library/org.swift.swiftpm/security/fingerprints`, `.build/`, and `Package.resolved`, then re-resolve |

## Version History

| Build | Date | Notes |
|-------|------|-------|
| 0.1.8 | 2026-03-01 | Side channel (`sideEval`): fire-and-forget Python eval via dedicated UDS socket per worker — safe to call during active streams; stream queue (`enqueue`/`dequeue`): ring-buffer data feed from Swift into Python generators with backpressure; proper `SideCommand` codec in `MessageFrame`; daemon thread shutdown hardened (stop flag + semaphore + GIL fence); 412 tests passing |
| 0.1.7+build.20260228 | 2026-02-28 | Fix: `sendResponse` write race — `sendLock: NSLock` serializes concurrent `send(2)` calls from main stream thread and Python daemon threads; regression test `testConcurrentDaemonThreadCallbackDuringStream`; warning fixes in test files; AGENTS.md macOS/Linux build split; 957 tests passing |
| 0.1.7+build.20260227 | 2026-02-27 | Memory-pressure-aware worker lifecycle; stream timeout socket drain; unified post-spawn setup (`configureSpawnedWorker`); dispatch_source_memorypressure actor isolation fix; MLX SIGBUS fix; worker `MSG_NOSIGNAL` on Linux; `persistentNamespace` cleanup on shutdown |
| 0.1.7 | — | See [GitHub Releases](https://github.com/mikhutchinson/swiftpython-commercial/releases) for prior release details |

## License

Commercial license. See LICENSE file for terms.
