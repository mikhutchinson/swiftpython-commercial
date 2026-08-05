import CryptoKit
import Foundation
import SwiftPythonRuntime

@main
enum DuplexSessionExample {
    static func main() async {
        do {
            try await withProcessPool(workers: 1) { pool in
                try await runFrameLoopback(pool: pool)
                try await runLogicalMessage(pool: pool)
            }
        } catch {
            let data = Data("DuplexSession failed: \(error)\n".utf8)
            try? FileHandle.standardError.write(contentsOf: data)
            Foundation.exit(EXIT_FAILURE)
        }
    }

    private static func runFrameLoopback(
        pool: PythonProcessPool
    ) async throws {
        let payload = Data("frame-loopback".utf8)
        let session = try await pool.openDuplexSession(
            handler: .eval(
                code: """
                from swift_duplex import InputFrame
                def run(session):
                    session.ready({"example": "frame-loopback"})
                    event = session.receive()
                    assert isinstance(event, InputFrame)
                    session.output.send(
                        bytes(event.buffer),
                        processed_input_through=event.sequence,
                    )
                    session.receive()
                    session.output.finish()
                """,
                entrypoint: "run"
            )
        )
        do {
            try await session.input.send(
                DuplexInputFrame(
                    payload: payload,
                    flags: [.independent]
                )
            )
            try await session.input.finish()
            var output = session.output.makeAsyncIterator()
            guard let frame = try await output.next(),
                  frame.buffer.copyData() == payload else {
                throw ExampleFailure.frameMismatch
            }
            try await session.acknowledgeOutput(
                consumedThrough: DuplexPosition(
                    sequence: frame.position.sequence,
                    byteOffset: frame.buffer.count
                )
            )
            guard try await output.next() == nil,
                  try await session.result().terminal == .completed else {
                throw ExampleFailure.badTerminal
            }
            await session.close()
            print("frame loopback: \(payload.count) bytes")
        } catch {
            await session.cancel(reason: .user)
            await session.close()
            throw error
        }
    }

    private static func runLogicalMessage(
        pool: PythonProcessPool
    ) async throws {
        let byteCount = 2 * 1_024 * 1_024 + 137
        let chunkBytes = 128 * 1_024
        let payload = Data(repeating: 0xA7, count: byteCount)
        let format = DuplexFormat(
            "video/hevc",
            metadata: ["profile": "main"]
        )
        var options = DuplexOptions.default
        options.inputFormat = format
        options.requirements = .messages
        options.limits.maximumFrameBytes = 256 * 1_024
        options.limits.maximumLogicalMessageBytes = 3 * 1_024 * 1_024
        options.limits.preferredMessageChunkBytes = chunkBytes
        options.limits.inputCreditBytes = 512 * 1_024
        options.limits.inputCreditFrames = 4

        let session = try await pool.openDuplexSession(
            handler: .eval(
                code: """
                import hashlib
                def run(session):
                    session.ready({"example": "logical-message"})
                    message = session.receive_message()
                    assert message.total_bytes == \(byteCount)
                    assert message.format == "video/hevc"
                    assert message.format_metadata == {"profile": "main"}
                    assert message.flags == 1
                    digest = hashlib.sha256()
                    chunks = 0
                    for chunk in message.chunks():
                        digest.update(chunk.buffer)
                        chunks += 1
                    assert chunks > 1
                    session.receive()
                    session.output.send(digest.digest())
                    session.output.finish()
                """,
                entrypoint: "run"
            ),
            options: options
        )
        do {
            try await session.input.sendMessage(
                payload,
                format: format,
                flags: [.independent]
            )
            try await session.input.finish()
            var output = session.output.makeAsyncIterator()
            guard let digest = try await output.next(),
                  digest.buffer.copyData()
                    == Data(SHA256.hash(data: payload)) else {
                throw ExampleFailure.digestMismatch
            }
            try await session.acknowledgeOutput(
                consumedThrough: DuplexPosition(
                    sequence: digest.position.sequence,
                    byteOffset: digest.buffer.count
                )
            )
            guard try await output.next() == nil,
                  try await session.result().terminal == .completed else {
                throw ExampleFailure.badTerminal
            }
            await session.close()
            print(
                "logical message: \(byteCount) bytes above "
                    + "\(options.limits.maximumFrameBytes)-byte frame ceiling"
            )
        } catch {
            await session.cancel(reason: .user)
            await session.close()
            throw error
        }
    }
}

enum ExampleFailure: Error {
    case frameMismatch
    case digestMismatch
    case badTerminal
}
