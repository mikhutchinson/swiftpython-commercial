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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.4/SwiftPythonRuntime.xcframework.zip",
            checksum: "9e4fd18990a083aecf9f6684f1eaac4f87f6d61c66f0406963c5495c9fd91dd7"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.4/SwiftPythonEngine.xcframework.zip",
            checksum: "79e27654538847d00699beee676a890c41aceaa3f7585bcb8ef7ca8fdc6a13b7"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.4/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "a4fabb65981719478693440f4a8b770c6ce6cb3079e703496f5513a88d6a05df"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.4/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "7ebf9e514aaa64fa4e17241363a5f407e1ea0b43a9767b6285ea6b75e86c2d6b"
        ),
    ]
)
