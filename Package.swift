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
            checksum: "3c955afd34cd32e88a0e91d386e009515b89408255b2b69d05ede9e14740bbf6"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8/SwiftPythonEngine.xcframework.zip",
            checksum: "64c50c9530bb1607f6f11913a68c3954813883af2307ad68fd47b3c212233770"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "9ee8d0caedcb97c478b559f82f7210613c5e7f040e3812378f78ade263bb4ba7"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "80a6530ecdf8827fc4bbd1402a7cafceda61008bc70a0b62bf8d21acf45503fd"
        ),
    ]
)
