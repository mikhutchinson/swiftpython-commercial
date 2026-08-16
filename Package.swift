// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "swiftpython-commercial-binaries",
    platforms: [
        .macOS(.v15)
    ],
    products: [
        .library(
            name: "SwiftPythonRuntime",
            targets: ["SwiftPythonRuntime", "SwiftPythonEngine"]
        ),
        .library(
            name: "SwiftPythonAudioInterop",
            targets: [
                "SwiftPythonAudioInterop", "SwiftPythonRuntime", "SwiftPythonEngine"
            ]
        ),
        .library(
            name: "SwiftPythonMetalInterop",
            targets: [
                "SwiftPythonMetalInterop", "SwiftPythonRuntime", "SwiftPythonEngine"
            ]
        ),
    ],
    targets: [
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.7/SwiftPythonRuntime.xcframework.zip",
            checksum: "7402c34dca8825e94dbabdd41c735eb25b438b408135938ef897afdc39c1a7cf"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.7/SwiftPythonEngine.xcframework.zip",
            checksum: "10f7b064107c08307e5f9e6d2428c79c13cff5426c66d1f07a7b7eb8b925db27"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.7/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "c8b4c428039d1c8de1697c91cde776c56c9b072c7b54a1238bccecc9c7121678"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.7/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "94380d1cea19a4221fb3243cd2271549cbd38f321595c1ab81e8c9b5dcc46fd8"
        ),
    ]
)
