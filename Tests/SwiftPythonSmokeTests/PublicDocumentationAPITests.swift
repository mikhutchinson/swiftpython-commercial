import Foundation
import SwiftPythonAudioInterop
import SwiftPythonMetalInterop
import SwiftPythonRuntime
import XCTest

/// Compile-time and value-shape coverage for public names used throughout
/// README.md and docs/api-guide. Runtime consumer behavior is exercised by the
/// standalone examples and scripts/consumer_path_smoke.sh.
final class PublicDocumentationAPITests: XCTestCase {
    func testDocumentedConfigurationShapes() throws {
        let audio = try DuplexAudioFormat(
            sampleRate: 24_000,
            channels: 1,
            sampleType: .signedInteger16,
            interleaving: .interleaved
        )
        XCTAssertEqual(audio.bytesPerFrame, 2)

        #if os(macOS)
            let hardwareProbe = try DuplexAudioHardwareProbeConfiguration(
                wireFormat: audio,
                durationSeconds: 2,
                timeoutSeconds: 30,
                requiresNonIdentityCaptureConversion: true
            )
            XCTAssertEqual(hardwareProbe.wireFormat, audio)
            XCTAssertEqual(hardwareProbe.durationSeconds, 2)
            XCTAssertEqual(hardwareProbe.timeoutSeconds, 30)
            _ = DuplexAudioHardwareProbeLauncher.permissionState
        #endif

        var duplex = DuplexOptions.default
        duplex.requirements = .managedBuffers
        duplex.limits.maximumLogicalMessageBytes = 10 * 1_024 * 1_024
        duplex.managedBuffers = ManagedBufferConfiguration(
            preset: .throughput,
            maximumBufferBytes: 16 * 1_024 * 1_024,
            maximumBufferedBytes: 32 * 1_024 * 1_024
        )
        XCTAssertEqual(duplex.requirements, .managedBuffers)

        let sandbox = SandboxConfiguration(
            runtimeAsset: URL(fileURLWithPath: "/tmp/base.img"),
            storageDirectory: URL(fileURLWithPath: "/tmp/sandboxes"),
            compute: .balanced,
            startup: .accelerated(
                checkpoint: URL(fileURLWithPath: "/tmp/release.swiftpython-snapshot"),
                credential: SandboxCredential(sealedBytes: Data("secret".utf8))
            ),
            network: .denied,
            workersPerSandbox: 1,
            minimumRuntimeVersion: "0.6.0-duplex.3",
            integrity: .strict
        )
        XCTAssertEqual(sandbox.integrity, .strict)
        XCTAssertEqual(sandbox.compute, .balanced)

        let ledger = DuplexCopyLedger()
        ledger.record(
            DuplexCopyLedgerEntry(
                segment: "documentation-compile",
                route: .ownedSharedCopy(reason: .explicitOwnedStorage),
                status: .boundedCPUCopy,
                logicalBytes: 16,
                copiedBytes: 16
            )
        )
        XCTAssertEqual(ledger.snapshot.copiedBytes, 16)
    }

    /// Never invoked. Keeping the calls in a type-checked function makes stale
    /// documentation entrypoint names fail the public binary-package test.
    private func compileDocumentedEntryPoints(
        pool: PythonProcessPool,
        sandbox: SandboxPool,
        tenant: SandboxTenant,
        session: PythonDuplexSession,
        shared: PyHandle,
        model: OwnedPyHandle
    ) async throws {
        let _: Double = try await pool.invokeResult(
            module: "math",
            function: "sqrt",
            args: [.python(144.0)]
        )
        let _: Double = try await pool.methodResult(
            handle: model,
            name: "score"
        )
        _ = try await pool.evalResult(
            "float(x.sum())",
            bindings: ["x": shared]
        ) as Double
        try await pool.respawnWorker(
            0,
            reason: .userInitiated,
            force: true
        )
        _ = try await pool.addWorkers(1)

        let stream: CancellableStream<Int> = try await pool.evalStream(
            "range(10)",
            options: .longRunning(timeout: 1_800)
        )
        _ = stream

        _ = try await pool.startOutputStream(
            generatorCode: "(b'x' for _ in range(1))",
            worker: 0,
            capacity: 64 * 1_024
        )
        let callback = try await pool.registerCallback(name: "docs_add") {
            @Sendable (a: Int, b: Int) -> Int in a + b
        }
        try await pool.unregisterCallback(name: callback.name)

        let dag = ProcessPoolDAG<String, Int>(nodes: [
            .init(id: "value") { context in
                try await context.worker.evalResult("42")
            },
        ])
        _ = try await pool.run(dag, maxParallelism: 1)

        _ = try await sandbox.execShell(
            tenantID: tenant.id,
            "python3 --version",
            options: ExecStreamOptions(timeout: 60)
        )
        _ = try await sandbox.execShellStream(
            tenantID: tenant.id,
            "python3 train.py"
        )
        _ = try await sandbox.execShellPTY(
            tenantID: tenant.id,
            "bash",
            options: ExecPTYOptions(
                initialSize: TerminalSize(columns: 120, rows: 32)
            )
        )

        try await session.input.send(
            DuplexInputFrame(
                payload: Data("hello".utf8),
                flags: [.independent]
            )
        )
        try await session.input.sendMessage(
            Data("message".utf8),
            format: DuplexFormat("application/octet-stream")
        )
        let lease = try await session.input.acquireManagedBuffer(
            byteCount: 4_096,
            alignment: .page
        )
        try await session.input.sendMessage(lease)
        _ = try await session.interrupt(reason: .inputActivity)
        try await session.acknowledgeOutput(
            consumedThrough: DuplexPosition(sequence: 1, byteOffset: 16)
        )

        #if os(macOS)
            let format = try DuplexAudioFormat(
                sampleRate: 24_000,
                channels: 1,
                sampleType: .signedInteger16,
                interleaving: .interleaved
            )
            let probe = try DuplexAudioHardwareProbeConfiguration(
                wireFormat: format,
                durationSeconds: 2,
                timeoutSeconds: 30,
                requiresNonIdentityCaptureConversion: true
            )
            let outcome = try await DuplexAudioHardwareProbeLauncher.run(
                configuration: probe
            )
            switch outcome {
            case let .ready(report):
                _ = report.metrics.captureHostTimestampFallbackCount
                _ = report.metrics.captureClockResetCount
                _ = report.metrics.captureHostClockResetCount
                _ = report.metrics.playbackInvalidSampleTimeCount
            case let .notReady(failure):
                _ = failure
            @unknown default:
                break
            }
        #endif
    }
}
