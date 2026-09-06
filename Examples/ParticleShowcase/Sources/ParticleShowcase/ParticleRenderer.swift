@preconcurrency import MetalKit
import Foundation

enum ShowcaseError: Error, CustomStringConvertible {
    case unavailable(String)
    case verification(String)

    var description: String {
        switch self {
        case .unavailable(let message), .verification(let message): return message
        }
    }
}

struct GPUReceipt: Codable, Sendable {
    let milliseconds: Double
    let particleBytesCopied: Int
    let sameAddress: Bool
    let sampleWords: [UInt32]
}

/// Mutable render work is called only by ParticleEngine. Presentation uses the
/// same ordered Metal command queue, and never refers to the shared tensor.
final class ParticleRenderer: @unchecked Sendable {
    let device: any MTLDevice
    let width: Int
    let height: Int
    let output: any MTLTexture
    private let light: any MTLTexture
    private let queue: any MTLCommandQueue
    private let points: any MTLRenderPipelineState
    private let development: any MTLRenderPipelineState
    private let presentation: any MTLRenderPipelineState
    private let probe: any MTLComputePipelineState
    private let probeOutput: any MTLBuffer

    init(width: Int = 1920, height: Int = 1080) throws {
        guard let device = MTLCreateSystemDefaultDevice(),
              let queue = device.makeCommandQueue() else {
            throw ShowcaseError.unavailable("This example requires a Metal device.")
        }
        self.device = device
        self.queue = queue
        self.width = width
        self.height = height
        let shaderURL = ShowcaseResources.bundle.url(forResource: "particles", withExtension: "metal")!
        let source = try String(contentsOf: shaderURL, encoding: .utf8)
        let library = try device.makeLibrary(source: source, options: nil)
        func pipeline(vertex: String, fragment: String, format: MTLPixelFormat,
                      additive: Bool = false) throws -> any MTLRenderPipelineState {
            let descriptor = MTLRenderPipelineDescriptor()
            descriptor.vertexFunction = library.makeFunction(name: vertex)
            descriptor.fragmentFunction = library.makeFunction(name: fragment)
            let color = descriptor.colorAttachments[0]!
            color.pixelFormat = format
            if additive {
                color.isBlendingEnabled = true
                color.sourceRGBBlendFactor = .one
                color.destinationRGBBlendFactor = .one
            }
            return try device.makeRenderPipelineState(descriptor: descriptor)
        }
        points = try pipeline(vertex: "particle_vertex", fragment: "particle_fragment",
                              format: .rgba16Float, additive: true)
        development = try pipeline(vertex: "full_screen", fragment: "develop", format: .bgra8Unorm)
        presentation = try pipeline(vertex: "full_screen", fragment: "present", format: .bgra8Unorm)
        guard let function = library.makeFunction(name: "sample_words"),
              let probeOutput = device.makeBuffer(length: 12 * 4, options: .storageModeShared) else {
            throw ShowcaseError.unavailable("Could not create the GPU verification buffer.")
        }
        probe = try device.makeComputePipelineState(function: function)
        self.probeOutput = probeOutput
        func texture(_ format: MTLPixelFormat, storage: MTLStorageMode) throws -> any MTLTexture {
            let descriptor = MTLTextureDescriptor.texture2DDescriptor(
                pixelFormat: format, width: width, height: height, mipmapped: false)
            descriptor.storageMode = storage
            descriptor.usage = [.renderTarget, .shaderRead]
            guard let texture = device.makeTexture(descriptor: descriptor) else {
                throw ShowcaseError.unavailable("Could not allocate a render target.")
            }
            return texture
        }
        light = try texture(.rgba16Float, storage: .private)
        output = try texture(.bgra8Unorm, storage: .shared)
    }

    /// The caller owns the tensor access scope through this synchronous return.
    /// No MTLBuffer wrapping those pages escapes; all GPU reads finish here.
    func render(_ values: UnsafeMutableBufferPointer<Float>, count: Int,
                scale: Float, pitch: Float, yaw: Float) throws -> GPUReceipt {
        try autoreleasepool {
            guard values.count == count * 4, let base = values.baseAddress else {
                throw ShowcaseError.verification("Unexpected particle tensor shape.")
            }
            let pointer = UnsafeMutableRawPointer(base)
            let length = values.count * MemoryLayout<Float>.stride
            let page = Int(getpagesize())
            let aligned = Int(bitPattern: pointer) % page == 0 && length % page == 0
            let particleBuffer: any MTLBuffer
            let copied: Int
            if device.hasUnifiedMemory && aligned {
                guard let buffer = device.makeBuffer(bytesNoCopy: pointer, length: length,
                                                     options: .storageModeShared, deallocator: nil) else {
                    throw ShowcaseError.unavailable("Metal could not map the shared particle pages.")
                }
                particleBuffer = buffer
                copied = 0
                guard buffer.contents() == pointer else {
                    throw ShowcaseError.verification("Metal did not preserve the shared address.")
                }
            } else {
                guard let buffer = device.makeBuffer(bytes: pointer, length: length, options: .storageModeShared) else {
                    throw ShowcaseError.unavailable("Could not upload the particle buffer.")
                }
                particleBuffer = buffer
                copied = length
            }
            guard let command = queue.makeCommandBuffer() else {
                throw ShowcaseError.unavailable("Could not create a Metal command buffer.")
            }
            let particlesPass = MTLRenderPassDescriptor()
            particlesPass.colorAttachments[0].texture = light
            particlesPass.colorAttachments[0].loadAction = .clear
            particlesPass.colorAttachments[0].storeAction = .store
            particlesPass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0)
            guard let encoder = command.makeRenderCommandEncoder(descriptor: particlesPass) else {
                throw ShowcaseError.unavailable("Could not create the particle render pass.")
            }
            var camera = SIMD4<Float>(pitch, yaw, scale, sqrt(1_048_576 / Float(count)))
            encoder.setRenderPipelineState(points)
            encoder.setVertexBuffer(particleBuffer, offset: 0, index: 0)
            encoder.setVertexBytes(&camera, length: MemoryLayout<SIMD4<Float>>.stride, index: 1)
            encoder.drawPrimitives(type: .point, vertexStart: 0, vertexCount: count)
            encoder.endEncoding()

            let finalPass = MTLRenderPassDescriptor()
            finalPass.colorAttachments[0].texture = output
            finalPass.colorAttachments[0].loadAction = .dontCare
            finalPass.colorAttachments[0].storeAction = .store
            guard let final = command.makeRenderCommandEncoder(descriptor: finalPass) else {
                throw ShowcaseError.unavailable("Could not create the color render pass.")
            }
            final.setRenderPipelineState(development)
            final.setFragmentTexture(light, index: 0)
            final.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
            final.endEncoding()

            guard let compute = command.makeComputeCommandEncoder() else {
                throw ShowcaseError.unavailable("Could not create the GPU probe.")
            }
            var n = UInt32(count)
            compute.setComputePipelineState(probe)
            compute.setBuffer(particleBuffer, offset: 0, index: 0)
            compute.setBuffer(probeOutput, offset: 0, index: 1)
            compute.setBytes(&n, length: 4, index: 2)
            compute.dispatchThreads(MTLSize(width: 12, height: 1, depth: 1),
                                    threadsPerThreadgroup: MTLSize(width: 12, height: 1, depth: 1))
            compute.endEncoding()
            command.commit()
            command.waitUntilCompleted()
            guard command.status == .completed else {
                throw ShowcaseError.verification("GPU failed: \(String(describing: command.error))")
            }
            let samples = Array(UnsafeBufferPointer(
                start: probeOutput.contents().assumingMemoryBound(to: UInt32.self), count: 12))
            return GPUReceipt(milliseconds: (command.gpuEndTime - command.gpuStartTime) * 1000,
                              particleBytesCopied: copied, sameAddress: particleBuffer.contents() == pointer,
                              sampleWords: samples)
        }
    }

    @MainActor
    func present(in view: MTKView) {
        guard let drawable = view.currentDrawable,
              let pass = view.currentRenderPassDescriptor,
              let command = queue.makeCommandBuffer(),
              let encoder = command.makeRenderCommandEncoder(descriptor: pass) else { return }
        encoder.setRenderPipelineState(presentation)
        encoder.setFragmentTexture(output, index: 0)
        encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
        encoder.endEncoding()
        command.present(drawable)
        command.commit()
    }
}
