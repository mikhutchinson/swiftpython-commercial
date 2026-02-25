# SwiftPython Commercial Runtime

Binary distribution of SwiftPythonRuntime for macOS, shipped as a prebuilt XCFramework with the SwiftPythonWorker sidecar. Use this package when building Swift apps that depend on [SwiftPython](https://github.com/mikhutchinson/swiftpython) (including demos and commercial applications).

## Contents

| Artifact | Description |
|----------|-------------|
| `SwiftPythonRuntime.xcframework` | Prebuilt static library + Swift module (macOS arm64) |
| `SwiftPythonWorker` | Sidecar executable for ProcessPool (multi-process Python) |

## Requirements

- **macOS** 15.0+
- **Swift** 6.0+
- **Python** 3.13 (Homebrew: `brew install python@3.13`)

## Installation

Add the package to your `Package.swift`:

```swift
dependencies: [
    .package(url: "https://github.com/mikhutchinson/swiftpython.git", branch: "main"),
    .package(url: "https://github.com/mikhutchinson/swiftpython-commercial.git", from: "0.1.0")
]
```

Use both packages together:

```swift
.target(
    name: "MyApp",
    dependencies: [
        .product(name: "SwiftPython", package: "swiftpython"),
        .product(name: "SwiftPythonRuntime", package: "swiftpython-commercial"),
        .product(name: "NumPy", package: "swiftpython"),
    ]
)
```

## Versioning

Releases follow semantic versioning and are tagged as `v0.1.0`, `v1.0.0`, etc. Check [Releases](https://github.com/mikhutchinson/swiftpython-commercial/releases) for the latest.

## Consumer Build Flow

Typical flow for apps using the commercial runtime:

```bash
export SWIFTPYTHON_COMMERCIAL_PACKAGE_URL=https://github.com/mikhutchinson/swiftpython-commercial.git
export SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION=0.1.0

./scripts/build-app-bundle.sh
```

Build scripts:
1. Resolve the commercial package via Swift Package Manager
2. Auto-discover `SwiftPythonWorker` from the SPM checkout (`.build/checkouts/swiftpython-commercial/SwiftPythonWorker`)
3. Bundle the worker with the app
4. Build against the binary `SwiftPythonRuntime` target

## Linker Requirements

Consumer apps must link against Python. Typical setup (Homebrew):

```
-L/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib
-lpython3.13
```

`libSwiftPythonRuntime.a` leaves Python C API symbols unresolved; the consumer links them.

## Worker Discovery

The runtime discovers `SwiftPythonWorker` in this order:

1. `SWIFTPYTHON_WORKER_PATH` environment variable
2. Bundled executable in the app bundle (`Bundle.main.path(forAuxiliaryExecutable: "SwiftPythonWorker")`)
3. Sibling to the main executable (same directory)
4. SPM checkout path (`.build/checkouts/swiftpython-commercial/SwiftPythonWorker`)

For production builds, set `SWIFTPYTHON_AUTOBUILD_WORKER=0` and ensure the worker is bundled or its path is set.

## Compatibility

| Platform | Architecture | Notes |
|----------|--------------|-------|
| macOS 15+ | arm64 (Apple Silicon) | Primary target |
| macOS 15+ | x86_64 | Via `--xcframework` build flag |

## License

Proprietary. See [LICENSE](LICENSE) for terms. You may link against and distribute apps built with this package, but you may not redistribute, reverse engineer, or modify the binaries themselves. SwiftPython source is licensed separately under [AGPL-3.0 + Commercial](https://github.com/mikhutchinson/SwiftPython/blob/main/LICENSE).
