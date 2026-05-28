import SwiftPythonRuntime
import XCTest

final class SwiftPythonSmokeTests: XCTestCase {
    func testPythonVersionIsReachable() async throws {
        let version: String = try await Python.run {
            try String(pythonObject: Python.sys.version)
        }
        XCTAssertTrue(version.contains("3.13"), "Expected Python 3.13, got: \(version)")
    }
}
