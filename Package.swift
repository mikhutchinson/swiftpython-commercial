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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8/SwiftPythonRuntime.xcframework.zip",
            checksum: "409ad7ef6f9165415613a2fb2c7331162a62e489ffacf29d3320d4bc1daeedf5"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8/SwiftPythonEngine.xcframework.zip",
            checksum: "84d3d8dd97328e18fc0b20d2bc78f9f3cdcf768bebdf186919d5c7615f836ac5"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "9d7ca9cbbf09024dd2cc628d2dcd1c094839d832504d510352687e3e86a13ab8"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "b197ea13a9edff7d4c5c8f4c498b1f42666ffb5f63092d3e8b30b48bae7c52f1"
        ),
    ]
)
