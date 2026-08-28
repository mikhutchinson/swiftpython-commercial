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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.1/SwiftPythonRuntime.xcframework.zip",
            checksum: "0e3604769f5357a84373e8d0bd4d8143b225424e4e98e7c694eda44d2e94d2cb"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.1/SwiftPythonEngine.xcframework.zip",
            checksum: "c4a844f2e40992bd22eacbc07747907702fa9ff855edf172ca22af12695a7147"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.1/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "90cf7c3a5bead9bb3cea3a62c52a3b081f9a041cd1b81e580b1cfe98dda43c0a"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.1/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "d1c8fff7b3a54193185ebfe808e09303a8f92a16c4c934dc8583b9b8b7269de9"
        ),
    ]
)
