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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.3/SwiftPythonWorkerService.xcframework.zip",
            checksum: "f5fdce038c745aef5f505db7a61ed87f5d79a791612e37605c02f4c055985fa3"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.3/SwiftPythonRuntime.xcframework.zip",
            checksum: "fc85063af5cb0de07140e5042917effd2a332df30c9185fd9ba233f9ace45b73"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.3/SwiftPythonEngine.xcframework.zip",
            checksum: "46932fc1b339174f78baa8a89cacb15fccd107e545fc28403d0ac9a5b698e09c"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.3/Python.xcframework.zip",
            checksum: "a2a3ce330e40d88dec577fc2ef2de2fc20ee95bb87a6dad2fdf1426ba7b91192"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.3/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "c9bf47bd85e4db1100779b103a7b63f1c2eb600b8e9caed108f1ff832c189b12"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.3/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "630012f877e58cfe351893d74595f4767d46e02027aca4a60167ed99376f742e"
        ),
    ]
)
