import CoreText
import CryptoKit
import Foundation
import SwiftPythonRuntime

enum ShowcaseResources {
    static let bundle: Bundle = {
        if let url = Bundle.main.url(forResource: "ParticleShowcase_ParticleShowcase", withExtension: "bundle"),
           let bundle = Bundle(url: url) { return bundle }
        return Bundle.module
    }()
}

enum Formation: Int, CaseIterable, Identifiable, Sendable {
    case galaxy, helix, wave, letters
    var id: Int { rawValue }
    var title: String { ["Galaxy", "Helix", "Wave", "Swift + Python"][rawValue] }
    var pitch: Float { [0.62, 0.18, 0.58, 0][rawValue] }
    var scale: Float { [0.56, 0.92, 0.82, 1.0][rawValue] }
    var caption: String {
        ["A million particles. One Python worker.", "Change the target. Keep the state.",
         "NumPy moves every point you see.", "Swift + Python. Same particle buffer."][rawValue]
    }

    static func at(seconds: Double) -> Formation {
        switch seconds {
        case ..<5: .galaxy
        case ..<10: .helix
        case ..<15: .wave
        default: .letters
        }
    }
}

struct FrameReceipt: Codable, Sendable {
    let frame: Int
    let simulationSeconds: Double
    let formation: Int
    let pythonMS: Double
    let stepAndRenderMS: Double
    let gpu: GPUReceipt
}

struct PythonProof: Decodable, Sendable {
    let pid: Int32
    let particles: Int
    let bytes: Int
    let dtype: String
    let finite: Bool
    let sha256: String
    let sample_words: [UInt32]
    let steps: Int
}

struct BufferProof: Codable, Sendable {
    let hostPID: Int32
    let workerPID: Int32
    let particleCount: Int
    let tensorBytes: Int
    let pythonSHA256: String
    let swiftSHA256: String
    let pythonAndGPUSamplesMatch: Bool
    let sameMetalAddress: Bool
    let particleBytesCopied: Int
    let finite: Bool
    let frame: Int
}

actor ParticleEngine {
    nonisolated let renderer: ParticleRenderer
    nonisolated let count: Int
    nonisolated let workerPID: Int32
    private let pool: PythonProcessPool
    private let tensor: PyHandle
    private var lastReceipt: FrameReceipt?
    private var pitch: Float = Formation.galaxy.pitch
    private var scale: Float = Formation.galaxy.scale
    private var stopped = false

    init(count: Int = 1_048_576) async throws {
        guard (1024...1_048_576).contains(count), count % 1024 == 0 else {
            throw ShowcaseError.unavailable("Use 1024...1048576 particles, in multiples of 1024.")
        }
        self.count = count
        renderer = try ParticleRenderer()
        let embeddedWorker = Bundle.main.bundleURL
            .appendingPathComponent("Contents/MacOS/SwiftPythonWorker")
        let worker = FileManager.default.isExecutableFile(atPath: embeddedWorker.path)
            ? embeddedWorker.path
            : ProcessInfo.processInfo.environment["SWIFTPYTHON_WORKER_PATH"]
                ?? Bundle.main.object(forInfoDictionaryKey: "SwiftPythonExampleWorkerPath") as? String
        let pool = try await PythonProcessPool(workers: 1, workerExecutablePath: worker)
        do {
            if let packages = Bundle.main.resourceURL?.appendingPathComponent("PythonPackages"),
               FileManager.default.fileExists(atPath: packages.path) {
                let encoded = try JSONSerialization.data(withJSONObject: [packages.path])
                let _: Int = try await pool.evalResult(
                    "import base64, json, sys\nsys.path.insert(0, json.loads(base64.b64decode('\(encoded.base64EncodedString())'))[0])\n0", worker: 0)
            }
            // Allocate the particle tensor first; validate actual page alignment
            // in the renderer instead of depending on the arena's layout.
            let tensor = try await pool.createManagedTensor(shape: [count, 4], dtype: .float32)
            let glyph = try await pool.createManagedTensor(shape: [256, 512], dtype: .uint8)
            try await pool.writeManagedTensor(Self.makeGlyphMask(), to: glyph)
            let url = ShowcaseResources.bundle.url(forResource: "particles", withExtension: "py")!
            let source = try String(contentsOf: url, encoding: .utf8)
            let pid: Int = try await pool.evalResult(
                source + "\nsimulation = ParticleSimulation(particle_tensor, glyph_mask)\nos.getpid()",
                bindings: ["particle_tensor": tensor, "glyph_mask": glyph], worker: 0, timeout: 30)
            guard pid != getpid() else {
                throw ShowcaseError.verification("The simulation must run in a separate Python worker.")
            }
            self.pool = pool
            self.tensor = tensor
            workerPID = Int32(pid)
        } catch {
            await pool.shutdown()
            throw error
        }
    }

    func frame(mode: Formation, seconds: Double, dt: Double = 1.0 / 30,
               burst: Int = 0, orbit: Float = 0) async throws -> FrameReceipt {
        guard !stopped, seconds.isFinite, dt > 0, dt <= 0.05, orbit.isFinite else {
            throw ShowcaseError.verification("Invalid frame request or stopped simulation.")
        }
        let start = ContinuousClock.now
        let result: [Double] = try await pool.evalResult(
            "simulation.step(\(mode.rawValue), \(seconds), \(dt), \(burst))", worker: 0, timeout: 10)
        guard result.count == 2, result.allSatisfy(\.isFinite) else {
            throw ShowcaseError.verification("Python returned an invalid frame receipt.")
        }
        pitch += (mode.pitch - pitch) * Float(1 - exp(-dt * 3))
        scale += (mode.scale - scale) * Float(1 - exp(-dt * 3))
        let renderer = renderer
        let count = count
        let pitch = pitch
        let scale = scale
        let yaw = mode == .letters ? orbit : Float(sin(seconds * 0.22)) * 0.12 + orbit
        let gpu = try await pool.withManagedTensor(tensor, as: Float.self) { values in
            try renderer.render(values, count: count, scale: scale, pitch: pitch, yaw: yaw)
        }
        let elapsed = start.duration(to: .now)
        let milliseconds = Double(elapsed.components.seconds) * 1000
            + Double(elapsed.components.attoseconds) / 1e15
        let receipt = FrameReceipt(frame: Int(result[1]), simulationSeconds: seconds,
                                   formation: mode.rawValue, pythonMS: result[0],
                                   stepAndRenderMS: milliseconds, gpu: gpu)
        lastReceipt = receipt
        return receipt
    }

    /// Call only between frames. Full host/Python hashes and a GPU word probe
    /// corroborate the same finished tensor generation, with no later writer.
    func verify() async throws -> BufferProof {
        guard let frame = lastReceipt, !stopped else {
            throw ShowcaseError.verification("Render a frame before requesting proof.")
        }
        let json: String = try await pool.evalResult(
            "import json\njson.dumps(simulation.proof())", worker: 0, timeout: 10)
        let python = try JSONDecoder().decode(PythonProof.self, from: Data(json.utf8))
        let digest = try await pool.withManagedTensor(tensor, as: Float.self) { values in
            var hasher = SHA256()
            hasher.update(bufferPointer: UnsafeRawBufferPointer(values))
            return hasher.finalize().map { String(format: "%02x", $0) }.joined()
        }
        guard python.pid == workerPID, python.particles == count,
              python.bytes == count * 16, python.dtype == "float32", python.finite,
              python.steps == frame.frame, python.sha256 == digest,
              python.sample_words == frame.gpu.sampleWords else {
            throw ShowcaseError.verification("Python, Swift and GPU particle proof disagreed.")
        }
        return BufferProof(hostPID: getpid(), workerPID: workerPID, particleCount: count,
                           tensorBytes: count * 16, pythonSHA256: python.sha256, swiftSHA256: digest,
                           pythonAndGPUSamplesMatch: true, sameMetalAddress: frame.gpu.sameAddress,
                           particleBytesCopied: frame.gpu.particleBytesCopied, finite: python.finite,
                           frame: frame.frame)
    }

    func shutdown() async {
        guard !stopped else { return }
        stopped = true
        await pool.shutdown()
    }

    private static func makeGlyphMask() throws -> [UInt8] {
        let width = 512, height = 256
        var pixels = [UInt8](repeating: 0, count: width * height)
        try pixels.withUnsafeMutableBytes { bytes in
            guard let context = CGContext(data: bytes.baseAddress, width: width, height: height,
                                          bitsPerComponent: 8, bytesPerRow: width,
                                          space: CGColorSpaceCreateDeviceGray(), bitmapInfo: 0) else {
                throw ShowcaseError.unavailable("Could not draw the typography target.")
            }
            context.setFillColor(gray: 0, alpha: 1)
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
            let font = CTFontCreateWithName("HelveticaNeue-Bold" as CFString, 103, nil)
            for (text, baseline) in [("SWIFT", 140.0), ("PYTHON", 24.0)] {
                let attributes: [NSAttributedString.Key: Any] = [
                    NSAttributedString.Key(kCTFontAttributeName as String): font,
                    NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 1, alpha: 1)
                ]
                let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attributes))
                let bounds = CTLineGetBoundsWithOptions(line, [])
                context.textPosition = CGPoint(x: (Double(width) - bounds.width) / 2, y: baseline)
                CTLineDraw(line, context)
            }
        }
        return pixels
    }
}
