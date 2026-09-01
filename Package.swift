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
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.3/SwiftPythonRuntime.xcframework.zip",
            checksum: "fb4e303aa3fdb1b54e017110a0c92d2935f875200982e64f4788c4fdc193688f"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.3/SwiftPythonEngine.xcframework.zip",
            checksum: "ec8434b5ac2a61bda3f9002a3a2db93dde591b5ea5f5fbe8a2818abae81a0e79"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.3/Python.xcframework.zip",
            checksum: "0ba40798090318426ac4b5a15afe02bbdfdbe227659838c6a7d027fb85fda18d"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.3/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "07788296a4e46f43964f6ce3a981d3707627da806f086c816b5ad63937a14860"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.3/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "078072d1bc8475d7411f17dd20e54a38288885ae57e79b0d2684043b0b54c017"
        ),
    ]
)
