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
    .package(url: "https://github.com/mikhutchinson/swiftpython-commercial.git", from: "0.1.7")
]
```

Or use environment variables for dynamic resolution:

```bash
export SWIFTPYTHON_COMMERCIAL_PACKAGE_URL=https://github.com/mikhutchinson/swiftpython-commercial.git
export SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION=0.1.7
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

## Troubleshooting

| Issue | Resolution |
|-------|------------|
| `Library not loaded: libpython3.13.dylib` | Ensure `PYTHONHOME` and `DYLD_LIBRARY_PATH` are set correctly |
| `compiled module was created by a different version of the compiler` | Rebuild your project with the same Swift version used to build this XCFramework |
| SPM fingerprint mismatch | Delete `~/Library/org.swift.swiftpm/security/fingerprints`, `.build/`, and `Package.resolved`, then re-resolve |

## Version History

| Build | Date | Notes |
|-------|------|-------|
| 0.1.7+build.20260227 | 2026-02-27 | Memory-pressure-aware worker lifecycle; stream timeout socket drain; unified post-spawn setup (`configureSpawnedWorker`); dispatch_source_memorypressure actor isolation fix; MLX SIGBUS fix; worker `MSG_NOSIGNAL` on Linux; `persistentNamespace` cleanup on shutdown |
| 0.1.7 | — | See [GitHub Releases](https://github.com/mikhutchinson/swiftpython-commercial/releases) for prior release details |

## License

Commercial license. See LICENSE file for terms.
