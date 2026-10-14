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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.6/SwiftPythonWorkerService.xcframework.zip",
            checksum: "1b0d9f8604590e233d6c6e653e3b3cf2c3389df6ca9d1b98c952cb3ed43cfd00"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.6/SwiftPythonRuntime.xcframework.zip",
            checksum: "64919ce47fed1e5edc34e18891cb8b13ba2225ae909fc7e4df2426a159566886"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.6/SwiftPythonEngine.xcframework.zip",
            checksum: "48d69ed814d72f13b69414266f1e75fec15141314a7744996e7683fc8ee8f93e"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.6/Python.xcframework.zip",
            checksum: "5a3a51a4178961c301524d2657911a29a7ce627428f46e8a5e12f813f7a489e2"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.6/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "9247260e0b76b3bb91d458dbbfea2c355ecb0d6f597050b1ce80aeaa2b27bd0b"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.6/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "bf48a60a5112e339494ab2f70caf04a3e586397cbab32ba816d820a073701b40"
        ),
    ]
)
