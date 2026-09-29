// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "swiftpython-commercial-binaries",
    platforms: [
        .macOS(.v15)
    ],
    products: [
        .library(
            name: "SwiftPythonWorkerService",
            targets: [
                "SwiftPythonWorkerService", "SwiftPythonRuntime", "SwiftPythonEngine", "Python"
            ]
        ),
        .library(
            name: "SwiftPythonRuntime",
            targets: ["SwiftPythonRuntime", "SwiftPythonEngine", "Python"]
        ),
        .library(
            name: "SwiftPythonAudioInterop",
            targets: [
                "SwiftPythonAudioInterop", "SwiftPythonRuntime", "SwiftPythonEngine", "Python"
            ]
        ),
        .library(
            name: "SwiftPythonMetalInterop",
            targets: [
                "SwiftPythonMetalInterop", "SwiftPythonRuntime", "SwiftPythonEngine", "Python"
            ]
        ),
    ],
    targets: [
        .binaryTarget(
            name: "SwiftPythonWorkerService",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1/SwiftPythonWorkerService.xcframework.zip",
            checksum: "37599232d8fbc38cd7d0e74411ee6727d3ea1b6e66ec32d56b9abd14f0346c2b"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1/SwiftPythonRuntime.xcframework.zip",
            checksum: "af8724b8f64bdbe60247d0684f47a9946e480942ef5266091baf25ad653295bf"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1/SwiftPythonEngine.xcframework.zip",
            checksum: "7a23684e03d08af5782295cc4f17c34912951bc522097bb1c5584befdd735028"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1/Python.xcframework.zip",
            checksum: "e0dd73d4726a4549f96f390f8d6310583f008fa6b5358da280da3d92b319d178"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "54eede8fdf8c2af07b1085cd983705e206e83baa374cbcb93e5d1e042f0c464e"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "e5864b33db7d7ba3956cc0eedc4dfd6ccf80f957e8e5082b65805e7849b159c9"
        ),
    ]
)
