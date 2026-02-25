// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SwiftPython",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "SwiftPythonRuntime", targets: ["SwiftPythonRuntime"]),
    ],
    targets: [
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "file:///Users/mikhutchinson/CascadeProjects/SwiftPython/.build/framework/SwiftPythonRuntime.zip",
            checksum: "dae6b1299e26f214d95f26cdabc100cf49dbfbe18ab8c3b3ddb8ca028a7e1e0d"
        ),
    ]
)
