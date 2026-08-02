import Foundation
import SwiftPythonAudioInterop
import SwiftPythonMetalInterop
import SwiftPythonRuntime
import XCTest

final class SwiftPythonSmokeTests: XCTestCase {
    func testPythonVersionIsReachable() async throws {
        let version: String = try await Python.run {
            try String(pythonObject: Python.sys.version)
        }
        XCTAssertTrue(version.contains("3.13"), "Expected Python 3.13, got: \(version)")
    }

    func testOptionalInteropModulesAreConsumerVisible() throws {
        let format = try DuplexAudioFormat(
            sampleRate: 16_000,
            channels: 1,
            sampleType: .signedInteger16,
            interleaving: .interleaved
        )
        XCTAssertEqual(format.bytesPerFrame, 2)

        let ledger = DuplexCopyLedger()
        ledger.record(
            DuplexCopyLedgerEntry(
                segment: "public-api-smoke",
                route: .ownedSharedCopy(reason: .explicitOwnedStorage),
                status: .boundedCPUCopy,
                logicalBytes: 32,
                copiedBytes: 32
            )
        )
        XCTAssertEqual(ledger.snapshot.observedBytes, 32)
        XCTAssertEqual(ledger.snapshot.copiedBytes, 32)
    }

    func testPublicDuplexConfigurationSurfaceIsConsumerVisible() throws {
        var requirements = DuplexSessionRequirements.messages
        requirements.minimumLogicalMessageBytes = 10 * 1_024 * 1_024

        var options = DuplexOptions.default
        options.requirements = requirements
        options.limits.maximumFrameBytes = 256 * 1_024
        options.limits.maximumLogicalMessageBytes = 12 * 1_024 * 1_024
        options.sharedBufferPool = DuplexSharedBufferPoolConfiguration(
            slotCount: 2,
            slotCapacity: 1 * 1_024 * 1_024,
            maximumOutstandingReferencedBytes: 2 * 1_024 * 1_024
        )

        XCTAssertTrue(options.requirements.requiredFeatures.contains(.messagesV1))
        XCTAssertLessThan(
            options.limits.maximumFrameBytes,
            options.limits.maximumLogicalMessageBytes
        )
        XCTAssertEqual(options.sharedBufferPool?.slotCount, 2)
        XCTAssertEqual(
            DuplexFormat(
                "video/hevc",
                metadata: ["profile": "main"]
            ).metadata["profile"],
            "main"
        )
    }

    func testCurrentTypedCapsuleAPI() async throws {
        final class CapsulePayload {
            let value = 42
        }

        let value: Int = try await Python.run {
            let payload = CapsulePayload()
            let capsule = try PyCapsuleRef(
                payload,
                name: "commercial.CapsulePayload"
            )
            let recovered: CapsulePayload = try PyCapsuleRef.extract(
                from: capsule.pyObject,
                name: "commercial.CapsulePayload"
            )
            return recovered.value
        }
        XCTAssertEqual(value, 42)
    }
}
