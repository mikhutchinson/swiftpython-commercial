import Foundation
import SwiftUI

@MainActor @Observable
final class IrisViewModel {
    var selectedKind: DatasetKind = .iris
    var selectedClassifier: ClassifierKind = .logisticRegression
    var useScaler = true
    var dataset: IrisDatasetPayload?
    var result: TrainingResult?
    var isLoading = false
    var isTraining = false
    var error: String?
    var workerPID: Int32?
    var xFeature = 0
    var yFeature = 1
    var mistakesOnly = false
    var selectedSampleID: Int?
    var experimentValues: [Double] = []
    var experiment: ExperimentPrediction?
    var isPredicting = false
    private var kernelTask: Task<IrisKernel, Error>?
    private var revision = 0
    private var stopping = false
    private var pendingPrediction: PredictionRequest?
    private var predictionTask: Task<Void, Never>?
    private var latestPredictionID = UUID()

    private struct PredictionRequest {
        let id: UUID
        let modelID: String
        let values: [Double]
    }

    var samples: [DataPoint] { dataset?.samples ?? [] }
    var predictionsByID: [Int: SamplePrediction] {
        Dictionary(uniqueKeysWithValues: (result?.predictions ?? []).map { ($0.id, $0) })
    }
    var visibleSamples: [DataPoint] {
        guard mistakesOnly, let result else { return samples }
        let ids = Set(result.mistakes.map(\.id))
        return samples.filter { ids.contains($0.id) }
    }
    var selectedSample: DataPoint? { samples.first { $0.id == selectedSampleID } }
    var experimentChanged: Bool { selectedSample.map { $0.values != experimentValues } ?? false }
    var hasModel: Bool { result != nil && !isLoading && !isTraining }

    func className(_ index: Int) -> String {
        guard let names = dataset?.classNames, names.indices.contains(index) else { return "Unknown" }
        return names[index]
    }

    func featureRange(_ index: Int) -> ClosedRange<Double> {
        let values = samples.map { $0.values[index] }
        let lower = values.min() ?? 0
        return lower...max(values.max() ?? 1, lower + 0.001)
    }

    private func kernel() async throws -> IrisKernel {
        guard !stopping else { throw CancellationError() }
        if kernelTask == nil { kernelTask = Task { try await IrisKernel() } }
        let service = try await kernelTask!.value
        workerPID = service.workerPID
        return service
    }

    func loadDataset(_ kind: DatasetKind) async {
        guard !stopping else { return }
        revision += 1
        let requestRevision = revision
        selectedKind = kind
        dataset = nil
        result = nil
        selectedSampleID = nil
        invalidatePrediction()
        mistakesOnly = false
        isLoading = true
        isTraining = false
        error = nil
        do {
            let service = try await kernel()
            let data = try await service.loadDataset(kind)
            guard revision == requestRevision, !stopping else { return }
            dataset = data
            xFeature = kind == .iris ? 2 : 0
            yFeature = kind == .iris ? 3 : 1
        } catch {
            guard revision == requestRevision, !stopping else { return }
            self.error = error.localizedDescription
        }
        if revision == requestRevision { isLoading = false }
    }

    func trainModel() async {
        guard dataset != nil, !isLoading, !isTraining, !stopping else { return }
        isTraining = true
        error = nil
        invalidatePrediction()
        let requestRevision = revision
        let kind = selectedKind
        let classifier = selectedClassifier
        let scale = useScaler
        do {
            let service = try await kernel()
            let trained = try await service.train(dataset: kind, classifier: classifier, useScaler: scale)
            guard revision == requestRevision, !stopping else { return }
            result = trained
            mistakesOnly = false
            selectedSampleID = trained.mistakes.first?.id ?? trained.predictions.first?.id
            isTraining = false
            resetExperiment()
        } catch {
            guard revision == requestRevision, !stopping else { return }
            self.error = "Training failed: \(error.localizedDescription)"
            // A failed request does not silently attribute an older model to new settings.
            result = nil
        }
        if revision == requestRevision { isTraining = false }
    }

    func resetExperiment() {
        invalidatePrediction()
        experimentValues = selectedSample?.values ?? []
        if hasModel { queuePrediction() }
    }

    func editFeature(_ index: Int, value: Double) {
        guard experimentValues.indices.contains(index), value.isFinite else { return }
        experimentValues[index] = value
        queuePrediction()
    }

    private func invalidatePrediction() {
        latestPredictionID = UUID()
        pendingPrediction = nil
        experiment = nil
    }

    private func queuePrediction() {
        guard let result, hasModel, !experimentValues.isEmpty, !stopping else { return }
        experiment = nil
        let request = PredictionRequest(id: UUID(), modelID: result.id, values: experimentValues)
        latestPredictionID = request.id
        pendingPrediction = request
        guard predictionTask == nil else { return }
        predictionTask = Task { [weak self] in await self?.drainPredictions() }
    }

    private func drainPredictions() async {
        isPredicting = true
        defer { isPredicting = false; predictionTask = nil }
        // One in-flight call plus one replaceable pending value. Slider events
        // cannot build an IPC backlog, and stale completions cannot update the UI.
        while let request = pendingPrediction, !stopping {
            pendingPrediction = nil
            do {
                let service = try await kernel()
                let prediction = try await service.predict(modelID: request.modelID, values: request.values)
                if latestPredictionID == request.id, result?.id == request.modelID, !stopping {
                    experiment = prediction
                }
            } catch {
                if latestPredictionID == request.id, !stopping { self.error = error.localizedDescription }
            }
        }
    }

    func restart() async {
        revision += 1
        invalidatePrediction()
        if let service = try? await kernelTask?.value { await service.shutdown() }
        kernelTask = nil
        workerPID = nil
        await loadDataset(selectedKind)
    }

    func stop() async {
        stopping = true
        revision += 1
        invalidatePrediction()
        if let service = try? await kernelTask?.value { await service.shutdown() }
        await predictionTask?.value
        kernelTask = nil
    }
}
