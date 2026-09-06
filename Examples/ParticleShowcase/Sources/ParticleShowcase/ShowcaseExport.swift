@preconcurrency import AVFoundation
import AppKit
import Foundation
import Metal

struct ExportOptions: Sendable {
    let directory: URL
    var seconds: Double = 22
    var fps: Int = 30

    func validate() throws {
        guard seconds.isFinite, (1...30).contains(seconds), (20...60).contains(fps) else {
            throw ShowcaseError.unavailable("Export supports 1...30 seconds and 20...60 fps.")
        }
    }
}

struct ExportReceipt: Codable {
    let device: String
    let particleCount: Int
    let width: Int
    let height: Int
    let playbackFPS: Int
    let frameCount: Int
    let playbackSeconds: Double
    let exportWallSeconds: Double
    let medianPythonMS: Double
    let medianStepAndRenderMS: Double
    let p95StepAndRenderMS: Double
    let particlePayloadRoute: String
    let imageRoute: String
    let proof: BufferProof
    let frames: [FrameReceipt]
}

@MainActor
enum ShowcaseExport {
    static func run(engine: ParticleEngine, options: ExportOptions,
                    progress: (Double, FrameReceipt) -> Void = { _, _ in }) async throws -> URL {
        try options.validate()
        let fileManager = FileManager.default
        try fileManager.createDirectory(at: options.directory, withIntermediateDirectories: true)
        let url = options.directory.appendingPathComponent("swiftpython-particles.mp4")
        guard !fileManager.fileExists(atPath: url.path) else {
            throw ShowcaseError.unavailable("The output video already exists. Choose a new directory.")
        }
        let renderer = engine.renderer
        let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: renderer.width,
            AVVideoHeightKey: renderer.height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: 16_000_000,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
                AVVideoMaxKeyFrameIntervalKey: options.fps * 2,
            ],
        ])
        input.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: renderer.width,
            kCVPixelBufferHeightKey as String: renderer.height,
            kCVPixelBufferCGImageCompatibilityKey as String: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
        ])
        guard writer.canAdd(input) else {
            throw ShowcaseError.unavailable("H.264 output is unavailable.")
        }
        writer.add(input)
        guard writer.startWriting() else {
            throw ShowcaseError.unavailable("Video writer failed: \(String(describing: writer.error))")
        }
        writer.startSession(atSourceTime: .zero)
        let started = ContinuousClock.now
        let frameCount = Int(options.seconds * Double(options.fps))
        var receipts = [FrameReceipt]()
        receipts.reserveCapacity(frameCount)
        do {
            for frame in 0..<frameCount {
                try Task.checkCancellation()
                let seconds = Double(frame) / Double(options.fps)
                let mode = Formation.at(seconds: seconds)
                let receipt = try await engine.frame(mode: mode, seconds: seconds,
                                                     dt: 1 / Double(options.fps))
                receipts.append(receipt)
                while !input.isReadyForMoreMediaData {
                    guard writer.status == .writing else {
                        throw ShowcaseError.unavailable("Video writer stopped: \(String(describing: writer.error))")
                    }
                    try await Task.sleep(for: .milliseconds(2))
                }
                try autoreleasepool {
                    guard let pool = adaptor.pixelBufferPool else {
                        throw ShowcaseError.unavailable("Video pixel pool is unavailable.")
                    }
                    var optionalPixel: CVPixelBuffer?
                    guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &optionalPixel) == kCVReturnSuccess,
                          let pixel = optionalPixel else {
                        throw ShowcaseError.unavailable("Could not allocate a video frame.")
                    }
                    CVPixelBufferLockBaseAddress(pixel, [])
                    defer { CVPixelBufferUnlockBaseAddress(pixel, []) }
                    guard let base = CVPixelBufferGetBaseAddress(pixel) else {
                        throw ShowcaseError.unavailable("Video frame has no backing storage.")
                    }
                    let stride = CVPixelBufferGetBytesPerRow(pixel)
                    // This is an explicit rendered-image readback for encoding;
                    // it is NOT included in the zero-copy particle-payload claim.
                    renderer.output.getBytes(base, bytesPerRow: stride,
                        from: MTLRegionMake2D(0, 0, renderer.width, renderer.height), mipmapLevel: 0)
                    guard let context = CGContext(data: base, width: renderer.width, height: renderer.height,
                                                  bitsPerComponent: 8, bytesPerRow: stride,
                                                  space: CGColorSpaceCreateDeviceRGB(),
                                                  bitmapInfo: CGBitmapInfo.byteOrder32Little.rawValue
                                                    | CGImageAlphaInfo.premultipliedFirst.rawValue) else {
                        throw ShowcaseError.unavailable("Could not create the video overlay.")
                    }
                    drawOverlay(context, width: renderer.width, height: renderer.height,
                                receipt: receipt, count: engine.count, mode: mode)
                    if [0, 4, 9, 14, 20].contains(Int(seconds)) && frame % options.fps == 0 {
                        if let image = context.makeImage() {
                            let bitmap = NSBitmapImageRep(cgImage: image)
                            if let png = bitmap.representation(using: .png, properties: [:]) {
                                try png.write(to: options.directory.appendingPathComponent("frame-\(Int(seconds)).png"))
                            }
                        }
                    }
                    guard adaptor.append(pixel, withPresentationTime: CMTime(value: Int64(frame), timescale: Int32(options.fps))) else {
                        throw ShowcaseError.unavailable("Could not append frame \(frame): \(String(describing: writer.error))")
                    }
                }
                progress(Double(frame + 1) / Double(frameCount), receipt)
                if frame % (options.fps * 2) == 0 {
                    print(String(format: "[export] %3d/%d  %@  Python %.2f ms  step+Metal %.2f ms",
                                 frame + 1, frameCount, mode.title, receipt.pythonMS, receipt.stepAndRenderMS))
                    fflush(nil)
                }
            }
            input.markAsFinished()
            await writer.finishWriting()
            guard writer.status == .completed else {
                throw ShowcaseError.unavailable("Video finalization failed: \(String(describing: writer.error))")
            }
            let proof = try await engine.verify()
            let elapsed = started.duration(to: .now)
            let seconds = Double(elapsed.components.seconds) + Double(elapsed.components.attoseconds) / 1e18
            let allNoCopy = receipts.allSatisfy { $0.gpu.particleBytesCopied == 0 && $0.gpu.sameAddress }
            let report = ExportReceipt(
                device: renderer.device.name, particleCount: engine.count,
                width: renderer.width, height: renderer.height, playbackFPS: options.fps,
                frameCount: frameCount, playbackSeconds: Double(frameCount) / Double(options.fps),
                exportWallSeconds: seconds, medianPythonMS: percentile(receipts.map(\.pythonMS), 0.5),
                medianStepAndRenderMS: percentile(receipts.map(\.stepAndRenderMS), 0.5),
                p95StepAndRenderMS: percentile(receipts.map(\.stepAndRenderMS), 0.95),
                particlePayloadRoute: allNoCopy ? "shared tensor -> scoped Metal bytesNoCopy; 0 particle payload bytes copied" : "Metal particle upload; see per-frame byte counts",
                imageRoute: "Metal BGRA render -> explicit CPU image readback -> H.264 encoder; export is offline, playback FPS is not live FPS",
                proof: proof, frames: receipts)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(report).write(to: options.directory.appendingPathComponent("receipt.json"))
            print("[verified] Python/Swift SHA-256 \(proof.swiftSHA256); GPU samples match")
            print("[saved] \(url.path)")
            return url
        } catch {
            writer.cancelWriting()
            throw error
        }
    }

    static func percentile(_ values: [Double], _ fraction: Double) -> Double {
        let ordered = values.sorted()
        return ordered[min(ordered.count - 1, Int(Double(ordered.count - 1) * fraction))]
    }

    private static func drawOverlay(_ context: CGContext, width: Int, height: Int,
                                    receipt: FrameReceipt, count: Int, mode: Formation) {
        // NSGraphicsContext draws in the bitmap's bottom-up coordinate system.
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
        defer { NSGraphicsContext.restoreGraphicsState() }
        let white = NSColor(white: 0.96, alpha: 1)
        let secondary = NSColor(white: 0.65, alpha: 1)
        let accent = NSColor(red: 1, green: 0.47, blue: 0.22, alpha: 1)
        func text(_ value: String, x: Double, y: Double, size: CGFloat,
                  color: NSColor = .white, weight: NSFont.Weight = .regular, mono: Bool = false) {
            let font = mono ? NSFont.monospacedDigitSystemFont(ofSize: size, weight: weight)
                : NSFont.systemFont(ofSize: size, weight: weight)
            (value as NSString).draw(at: NSPoint(x: x, y: y), withAttributes: [.font: font, .foregroundColor: color])
        }
        let top = Double(height)
        text(count.formatted(), x: 68, y: top - 140, size: 84, color: white, weight: .semibold, mono: true)
        text("PARTICLES  /  COMPUTED IN PYTHON", x: 73, y: top - 174, size: 20, color: secondary, weight: .medium)
        text("SwiftPython", x: Double(width) - 333, y: top - 108, size: 38, color: white, weight: .semibold)
        text("NumPy  →  shared tensor  →  Metal", x: Double(width) - 421, y: top - 144, size: 19, color: secondary)
        text(mode.caption, x: 72, y: 134, size: 36, color: white, weight: .medium)
        context.setStrokeColor(NSColor(white: 0.22, alpha: 1).cgColor)
        context.move(to: CGPoint(x: 72, y: 109))
        context.addLine(to: CGPoint(x: width - 72, y: 109))
        context.strokePath()
        let bytes = count * 16
        let bufferSize = bytes % 1_048_576 == 0
            ? "\(bytes / 1_048_576) MiB" : "\(bytes / 1024) KiB"
        text(String(format: "PYTHON  %.1f ms", receipt.pythonMS), x: 74, y: 62, size: 23, color: accent, weight: .medium, mono: true)
        text(String(format: "METAL  %.1f ms", receipt.gpu.milliseconds), x: 405, y: 62, size: 23, color: secondary, mono: true)
        text("\(bufferSize) PARTICLE BUFFER", x: 725, y: 62, size: 23, color: secondary, mono: true)
        let route = receipt.gpu.particleBytesCopied == 0 ? "0 PARTICLE PAYLOAD COPIES" : "PARTICLE UPLOAD COPY"
        text(route, x: 1170, y: 62, size: 23, color: secondary, mono: true)
        text("OFFLINE EXPORT · MEASURED COMPUTE TIMINGS", x: Double(width) - 539, y: 26, size: 13, color: secondary)
    }
}
