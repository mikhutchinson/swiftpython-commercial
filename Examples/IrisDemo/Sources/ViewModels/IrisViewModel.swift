import Foundation
import SwiftUI

@MainActor
final class IrisViewModel: ObservableObject {
    @Published var selectedKind: DatasetKind = .iris
    @Published var featureNames: [String] = []
    @Published var classNames: [String] = []
    @Published var points: [DataPoint] = []
    @Published var featureStats: [FeatureStat] = []
    @Published var classDistribution: [ClassCount] = []
    @Published var isLoading = false
    @Published var error: String?
    @Published var isTraining = false
    @Published var selectedClassifier: ClassifierKind = .logisticRegression
    @Published var useScaler: Bool = true
    @Published var trainingResult: TrainingResult?

    func loadDataset() async {
        await load(kind: selectedKind)
    }

    func load(kind: DatasetKind) async {
        isLoading = true
        error = nil
        trainingResult = nil
        do {
            let payload = try await IrisKernel.loadDataset(kind)
            try apply(payload)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    func trainModel() async {
        guard !points.isEmpty, !featureNames.isEmpty else { return }
        isTraining = true
        trainingResult = nil
        error = nil
        do {
            trainingResult = try await IrisKernel.train(
                dataset: selectedKind,
                classifier: selectedClassifier,
                useScaler: useScaler
            )
        } catch {
            self.error = "Training failed: \(error.localizedDescription)"
        }
        isTraining = false
    }

    private func apply(_ payload: IrisDatasetPayload) throws {
        guard payload.points.count == payload.targets.count else {
            throw IrisKernelError.invalidPayload("Python returned mismatched feature and target counts")
        }
        featureNames = payload.featureNames
        classNames = payload.classNames
        points = zip(payload.points, payload.targets).map { values, classId in
            DataPoint(values: values, classId: classId)
        }
        computeFeatureStats()
        computeClassDistribution()
    }

    private func computeFeatureStats() {
        guard let nFeatures = points.first?.values.count, nFeatures > 0 else {
            featureStats = []
            return
        }

        featureStats = (0..<nFeatures).map { featureIndex in
            var sum = 0.0
            var sumSq = 0.0
            var minVal = Double.infinity
            var maxVal = -Double.infinity

            for point in points {
                let value = point.value(at: featureIndex)
                sum += value
                sumSq += value * value
                minVal = min(minVal, value)
                maxVal = max(maxVal, value)
            }

            let count = Double(points.count)
            let mean = sum / count
            let variance = (sumSq / count) - (mean * mean)
            let std = variance > 0 ? sqrt(variance) : 0
            let name = featureNames.indices.contains(featureIndex) ? featureNames[featureIndex] : "F\(featureIndex + 1)"

            return FeatureStat(
                name: name,
                mean: mean,
                std: std,
                min: minVal,
                max: maxVal
            )
        }
    }

    private func computeClassDistribution() {
        let counts = Dictionary(grouping: points, by: \.classId).mapValues(\.count)
        classDistribution = classNames.enumerated().compactMap { index, name in
            guard let count = counts[index] else { return nil }
            return ClassCount(className: name, count: count)
        }
    }
}
