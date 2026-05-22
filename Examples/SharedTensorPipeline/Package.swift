// swift-tools-version: 6.0
import Foundation
import PackageDescription

private func parseSemVer(_ raw: String) -> Version? {
    let core = raw
        .split(separator: "-", maxSplits: 1, omittingEmptySubsequences: true)
        .first
        .map(String.init) ?? raw
    let parts = core.split(separator: ".", omittingEmptySubsequences: false)
    guard parts.count == 3,
          let major = Int(parts[0]),
          let minor = Int(parts[1]),
          let patch = Int(parts[2]) else {
        return nil
    }
    return Version(major, minor, patch)
}

private struct SwiftPythonDependencyConfig {
    let dependency: Package.Dependency
    let packageName: String
}

private func packageNameFromDependencyURL(_ rawURL: String) -> String {
    var trimmed = rawURL.trimmingCharacters(in: .whitespacesAndNewlines)
    while trimmed.hasSuffix("/") {
        trimmed.removeLast()
    }
    let base = (trimmed as NSString).lastPathComponent
    if base.hasSuffix(".git") {
        return String(base.dropLast(4))
    }
    return base
}

private func makeSwiftPythonDependencyConfig(defaultPath: String = "../..") -> SwiftPythonDependencyConfig {
    let env = ProcessInfo.processInfo.environment
    if let url = env["SWIFTPYTHON_COMMERCIAL_PACKAGE_URL"]?
        .trimmingCharacters(in: .whitespacesAndNewlines),
       !url.isEmpty,
       let versionRaw = env["SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION"]?
        .trimmingCharacters(in: .whitespacesAndNewlines),
       !versionRaw.isEmpty {
        guard let version = parseSemVer(versionRaw) else {
            fatalError("SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION must be semantic version MAJOR.MINOR.PATCH")
        }
        return SwiftPythonDependencyConfig(
            dependency: .package(url: url, exact: version),
            packageName: packageNameFromDependencyURL(url)
        )
    }

    return SwiftPythonDependencyConfig(
        dependency: .package(path: defaultPath),
        packageName: "swiftpython-commercial"
    )
}

private func swiftPythonConfig() -> SwiftPythonDependencyConfig {
    makeSwiftPythonDependencyConfig()
}

let package = Package(
    name: "SharedTensorPipeline",
    platforms: [.macOS(.v15)],
    dependencies: [
        swiftPythonConfig().dependency,
    ],
    targets: [
        .executableTarget(
            name: "SharedTensorPipeline",
            dependencies: [
                .product(name: "SwiftPythonRuntime", package: swiftPythonConfig().packageName),
            ],
            linkerSettings: [
                .unsafeFlags([
                    "-L/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib",
                    "-lpython3.13",
                ]),
            ]
        ),
    ]
)
