// swift-tools-version: 6.0
import PackageDescription
import Foundation

private func parseSemVer(_ raw: String) -> Version? {
    let buildSplit = raw.split(
        separator: "+",
        maxSplits: 1,
        omittingEmptySubsequences: false
    )
    guard buildSplit.count <= 2 else { return nil }

    let releaseAndPrerelease = buildSplit[0].split(
        separator: "-",
        maxSplits: 1,
        omittingEmptySubsequences: false
    )
    let core = releaseAndPrerelease[0].split(
        separator: ".",
        omittingEmptySubsequences: false
    )
    guard core.count == 3,
          let major = Int(core[0]),
          let minor = Int(core[1]),
          let patch = Int(core[2]),
          major >= 0,
          minor >= 0,
          patch >= 0 else {
        return nil
    }

    let prerelease = releaseAndPrerelease.count == 2
        ? releaseAndPrerelease[1].split(
            separator: ".",
            omittingEmptySubsequences: false
        ).map(String.init)
        : []
    let build = buildSplit.count == 2
        ? buildSplit[1].split(
            separator: ".",
            omittingEmptySubsequences: false
        ).map(String.init)
        : []
    guard prerelease.allSatisfy({ !$0.isEmpty }),
          build.allSatisfy({ !$0.isEmpty }) else {
        return nil
    }
    return Version(
        major,
        minor,
        patch,
        prereleaseIdentifiers: prerelease,
        buildMetadataIdentifiers: build
    )
}

private let swiftPythonPackageName = "swiftpython-commercial"

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
            fatalError(
                "SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION must be semantic version MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]"
            )
        }
        return SwiftPythonDependencyConfig(
            dependency: .package(url: url, exact: version),
            packageName: packageNameFromDependencyURL(url)
        )
    }

    return SwiftPythonDependencyConfig(
        dependency: .package(name: swiftPythonPackageName, path: defaultPath),
        packageName: swiftPythonPackageName
    )
}

private func swiftPythonConfig() -> SwiftPythonDependencyConfig {
    makeSwiftPythonDependencyConfig()
}

let package = Package(
    name: "ParticleShowcase",
    platforms: [.macOS(.v15)],
    products: [.executable(name: "particle-showcase", targets: ["ParticleShowcase"])],
    dependencies: [swiftPythonConfig().dependency],
    targets: [
        .executableTarget(
            name: "ParticleShowcase",
            dependencies: [.product(name: "SwiftPythonRuntime", package: swiftPythonConfig().packageName)],
            resources: [.copy("Resources/particles.py"), .copy("Resources/particles.metal")],
            swiftSettings: [.unsafeFlags(["-parse-as-library"])]
        ),
    ]
)
