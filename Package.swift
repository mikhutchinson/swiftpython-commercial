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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.2/SwiftPythonWorkerService.xcframework.zip",
            checksum: "b9ebd79124b87af1e68add5412c2a3f2268f32e23dda7b481c9f682e84f2b6a7"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.2/SwiftPythonRuntime.xcframework.zip",
            checksum: "bce827c1bffe3e48128ca540d5af8b0242081fc921d5bfb35e57aa592aa3e40e"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.2/SwiftPythonEngine.xcframework.zip",
            checksum: "ac0fa85bb205f2beaba137f2a5e19012f8909e83f2dc999a1b3e1c773ad51af3"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.2/Python.xcframework.zip",
            checksum: "e19c59680a4e02094054d81015aafaf1fd9e5e4968ea7e9a33e6e10ee72f774f"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.2/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "1641efd0432d09542a05febf87903509eecbf3cf29d907c47e0a849279132b6f"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.2/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "28f81a2909725c9fd70669d30c629b2ab45dd1862b0f91dec1bbbff7f79ca034"
        ),
    ]
)
