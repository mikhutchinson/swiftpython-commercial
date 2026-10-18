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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.7/SwiftPythonWorkerService.xcframework.zip",
            checksum: "2422cc0c2fb2d57797ec5fe1ce644883e141e82a11d95890cd46024195503f32"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.7/SwiftPythonRuntime.xcframework.zip",
            checksum: "9386c2cbbdf1e12d8e7548c82f9e0458d0e3eed16ff5d04c9e3554cb880db0fe"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.7/SwiftPythonEngine.xcframework.zip",
            checksum: "213cff5cb4cd44888e173a45020a1988e1ca0fa7790c1f748912d177b3c0cb0d"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.7/Python.xcframework.zip",
            checksum: "d4be19754cc331d9e9aad2b23f678563766fef84879e9b109091984c5985c813"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.7/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "e85cd5cad5af5961ddcf3b81b85b7f48b72aa880f0df8a84f9782f64be8abbab"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.7/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "297980ed730bc969321c227af547b957d99e3e74af7964c12476d515b3d44787"
        ),
    ]
)
