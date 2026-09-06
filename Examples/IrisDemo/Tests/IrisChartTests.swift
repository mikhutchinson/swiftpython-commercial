import AppKit
import SwiftUI
import XCTest
@testable import IrisDemo

final class IrisChartTests: XCTestCase {
    @MainActor
    func testSelectingPointsAndShowingMistakes() throws {
        let model = IrisViewModel()
        model.dataset = dataset(featureCount: 4, classCount: 3)
        let host = makeHost(model)
        let unselected = try render(host)

        for id in 0..<6 {
            model.selectedSampleID = id
            XCTAssertNotEqual(try render(host), unselected, "Selection must change the rendered chart")
        }
        model.result = TrainingResult(
            id: "test", dataset: "iris", classifier: "test", useScaler: true,
            trainCount: 3, testCount: 3, testAccuracy: 0, cvAccuracyMean: 0,
            cvAccuracyStd: 0, confusionMatrix: [], classificationReport: "",
            learningCurveTrainSizes: [], learningCurveMeanTrainScores: [],
            learningCurveMeanTestScores: [], predictions: (0..<3).map {
                SamplePrediction(id: $0, actual: $0, predicted: ($0 + 1) % 3,
                                 probabilities: [0.2, 0.3, 0.5])
            }, elapsedSeconds: 0)
        try render(host)
        model.mistakesOnly = true
        try render(host)
        model.selectedSampleID = nil
        try render(host)
        model.mistakesOnly = false
        model.result = nil
        try render(host)
    }

    @MainActor
    func testReplacingDatasetWhileChartRetainsContent() throws {
        let model = IrisViewModel()
        model.dataset = dataset(featureCount: 30, classCount: 2)
        model.xFeature = 28
        model.yFeature = 29
        let host = makeHost(model)
        try render(host)

        for _ in 0..<3 {
            model.dataset = nil
            try render(host)
            // Exercise publication before axis normalization, as in loadDataset.
            model.dataset = dataset(featureCount: 4, classCount: 3)
            try render(host)
            model.xFeature = 2
            model.yFeature = 3
            try render(host)
            model.dataset = dataset(featureCount: 30, classCount: 2)
            model.xFeature = 28
            model.yFeature = 29
            try render(host)
        }
    }

    private func dataset(featureCount: Int, classCount: Int) -> IrisDatasetPayload {
        IrisDatasetPayload(
            featureNames: (0..<featureCount).map { "Feature \($0)" },
            classNames: (0..<classCount).map { "Class \($0)" },
            points: (0..<6).map { row in
                (0..<featureCount).map { column in Double(row + column) }
            },
            targets: (0..<6).map { $0 % classCount })
    }

    @MainActor
    private func makeHost(_ model: IrisViewModel) -> NSHostingView<IrisScatterPlot> {
        _ = NSApplication.shared
        let host = NSHostingView(rootView: IrisScatterPlot(model: model))
        host.frame = NSRect(x: 0, y: 0, width: 800, height: 400)
        return host
    }

    @MainActor
    @discardableResult
    private func render(_ host: NSHostingView<IrisScatterPlot>) throws -> Data {
        host.layoutSubtreeIfNeeded()
        // Flush deferred observation/Charts layout while the old view is retained.
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.03))
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        XCTAssertGreaterThan(bitmap.pixelsWide, 0)
        return try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
    }
}
