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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0/SwiftPythonWorkerService.xcframework.zip",
            checksum: "8a1b41f1fe575a29c56ac899a84f59ea4a86711ac742f56f6d01058fc01dcf87"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0/SwiftPythonRuntime.xcframework.zip",
            checksum: "625464cda34afa30c53a2a0fde99f32757690960348aa71e447f8e9d8e061040"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0/SwiftPythonEngine.xcframework.zip",
            checksum: "2c4049b7f0d73c1a28f2343676a635be45149627c208b7edcae44432e38f8d8a"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0/Python.xcframework.zip",
            checksum: "f668b72e2f122f1920860aefeb121ea02f87db00fe938e0a7a7d15525d73171c"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "458da25219f2104c3ca94ec866c0cd9305d3ed76e001a431c5b39c44140dd9bc"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "cb46fbf05faa6609080353508dcc3f3d9e72416b8a413db3b6aa11c535ad20be"
        ),
    ]
)
