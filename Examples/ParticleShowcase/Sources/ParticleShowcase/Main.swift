import AppKit
import Foundation
import SwiftPythonRuntime

@main
struct ShowcaseMain {
    @MainActor
    static func main() async {
        do {
            setenv("PYTHONDONTWRITEBYTECODE", "1", 1)
            setenv("SWIFTPYTHON_AUTOBUILD_WORKER", "0", 0)
            var arguments = Array(CommandLine.arguments.dropFirst())
            if arguments.contains("--help") {
                print("""
                SwiftPython Particle Showcase
                  particle-showcase                              Open the live Mac app
                  particle-showcase --export DIRECTORY           Write a 22s 1080p clip + receipt
                  particle-showcase --smoke DIRECTORY             Verify all four formations
                Options for headless runs: --particles N (default 1048576)
                Export options: --seconds 1...30 --fps 20...60 (default 22s / 30fps)
                Use run.sh to build and select this checkout's worker.
                """)
                return
            }
            var exportDirectory: URL?
            var smokeDirectory: URL?
            var count = 1_048_576
            var seconds = 22.0
            var fps = 30
            while !arguments.isEmpty {
                let flag = arguments.removeFirst()
                guard !arguments.isEmpty else { throw ShowcaseError.unavailable("Missing value for \(flag)") }
                let value = arguments.removeFirst()
                switch flag {
                case "--export": exportDirectory = URL(fileURLWithPath: value, isDirectory: true)
                case "--smoke": smokeDirectory = URL(fileURLWithPath: value, isDirectory: true)
                case "--particles":
                    guard let parsed = Int(value) else { throw ShowcaseError.unavailable("Invalid particle count.") }
                    count = parsed
                case "--seconds":
                    guard let parsed = Double(value) else { throw ShowcaseError.unavailable("Invalid duration.") }
                    seconds = parsed
                case "--fps":
                    guard let parsed = Int(value) else { throw ShowcaseError.unavailable("Invalid frame rate.") }
                    fps = parsed
                default: throw ShowcaseError.unavailable("Unknown option: \(flag)")
                }
            }
            guard exportDirectory == nil || smokeDirectory == nil else {
                throw ShowcaseError.unavailable("Choose export or smoke, not both.")
            }
            if let directory = exportDirectory ?? smokeDirectory {
                let options = ExportOptions(directory: directory, seconds: seconds, fps: fps)
                try options.validate()
                let engine = try await ParticleEngine(count: count)
                print("[worker] \(engine.workerPID) host=\(getpid()) device=\(engine.renderer.device.name)")
                do {
                    if smokeDirectory != nil {
                        try await smoke(engine: engine, directory: directory)
                    } else {
                        _ = try await ShowcaseExport.run(engine: engine, options: options)
                    }
                    await engine.shutdown()
                } catch {
                    await engine.shutdown()
                    throw error
                }
                guard kill(engine.workerPID, 0) == -1 && errno == ESRCH else {
                    throw ShowcaseError.verification("Owned worker was not reaped after shutdown.")
                }
                print("[cleanup] owned worker reaped")
                fflush(nil)
                _Exit(0)
            } else {
                guard CommandLine.arguments.count == 1 else {
                    throw ShowcaseError.unavailable("Headless options require --export or --smoke.")
                }
                NSApplication.shared.setActivationPolicy(.regular)
                ParticleShowcaseApp.main()
            }
        } catch {
            FileHandle.standardError.write(Data("Particle Showcase: \(error)\n".utf8))
            fflush(nil)
            _Exit(1)
        }
    }

    @MainActor
    private static func smoke(engine: ParticleEngine, directory: URL) async throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var proofs = [BufferProof]()
        for mode in Formation.allCases {
            for step in 0..<30 {
                let seconds = Double(mode.rawValue * 30 + step) / 30
                _ = try await engine.frame(mode: mode, seconds: seconds, burst: mode.rawValue)
            }
            let proof = try await engine.verify()
            proofs.append(proof)
            print("[verified] \(mode.title): \(proof.particleCount) finite particles, shared hash + GPU samples match")
        }
        guard Set(proofs.map(\.swiftSHA256)).count == 4 else {
            throw ShowcaseError.verification("Formations did not change the particle tensor.")
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(proofs).write(to: directory.appendingPathComponent("smoke-proof.json"))
    }
}
