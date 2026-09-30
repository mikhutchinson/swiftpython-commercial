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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.1/SwiftPythonWorkerService.xcframework.zip",
            checksum: "2deed51a82eca3a9edb042ccf9dfa14bd88d9edf34f24722b54a98d1ec76bfd9"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.1/SwiftPythonRuntime.xcframework.zip",
            checksum: "8066f34e7ef2aa69a3ce50c4b2d8bcb4ad8977a7663432d2774ac94652c770cb"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.1/SwiftPythonEngine.xcframework.zip",
            checksum: "04a074f462ee6fd5ba0c91c72960b35484ff908c00ab9e1e690d6b3fa093d643"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.1/Python.xcframework.zip",
            checksum: "d30c365f4e3a61a00920600f2d193c5ec202e1b072a2745c146398d12939e6fd"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.1/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "924ac5ef84b291643768c7cefd8dfa599e79cbb2f93f73c16899be38c05e459d"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.1/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "9498e03549fc89d22732c299c640402ce149f322814cc980e347454644c7a570"
        ),
    ]
)
