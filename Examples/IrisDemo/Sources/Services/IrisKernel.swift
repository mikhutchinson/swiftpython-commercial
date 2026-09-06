import Foundation
import SwiftPythonRuntime

/// One worker owns the imported service, cached datasets, and fitted model.
/// Only ordinary Sendable values cross back into the UI.
actor IrisKernel {
    nonisolated let workerPID: Int32
    private let pool: PythonProcessPool
    private var stopped = false

    init() async throws {
        var worker = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/SwiftPythonWorker")
        #if SWIFTPYTHON_SOURCE_DEMO
        if !FileManager.default.isExecutableFile(atPath: worker.path),
           let path = ProcessInfo.processInfo.environment["SWIFTPYTHON_WORKER_PATH"]
                ?? Bundle.main.object(forInfoDictionaryKey: "SwiftPythonExampleWorkerPath") as? String {
            worker = URL(fileURLWithPath: path)
        }
        #endif
        guard FileManager.default.isExecutableFile(atPath: worker.path) else {
            throw IrisKernelError.missingResource("SwiftPythonWorker")
        }
        let resources = Bundle.main.url(forResource: "IrisDemo_IrisDemo", withExtension: "bundle")
            .flatMap(Bundle.init(url:)) ?? Bundle.module
        guard let service = resources.url(forResource: "iris_kernel", withExtension: "py", subdirectory: "Python")
                ?? resources.url(forResource: "iris_kernel", withExtension: "py") else {
            throw IrisKernelError.missingResource("Iris service")
        }
        var paths = [service.deletingLastPathComponent().path]
        if let packages = Bundle.main.resourceURL?.appendingPathComponent("PythonPackages"),
           FileManager.default.fileExists(atPath: packages.path) {
            paths.insert(packages.path, at: 0)
        } else {
            #if !SWIFTPYTHON_SOURCE_DEMO
            throw IrisKernelError.missingResource("PythonPackages")
            #endif
        }
        let pool = try await PythonProcessPool(workers: 1, workerExecutablePath: worker.path, blasThreads: 1)
        do {
            let encodedPaths = try JSONEncoder().encode(paths).base64EncodedString()
            let pid: Int = try await pool.evalResult(
                "import base64, json, os, sys\nsys.path[:0] = json.loads(base64.b64decode('\(encodedPaths)'))\nimport iris_kernel\nos.getpid()",
                worker: 0, timeout: 30)
            guard pid != getpid() else { throw IrisKernelError.invalidPayload("Expected a separate Python worker") }
            workerPID = Int32(pid)
            self.pool = pool
        } catch {
            await pool.shutdown()
            throw error
        }
    }

    func loadDataset(_ kind: DatasetKind) async throws -> IrisDatasetPayload {
        let json: String = try await pool.invokeResult(
            module: "iris_kernel", function: "load_dataset", args: [kind.pythonKey], worker: 0, timeout: 15)
        let payload = try decode(IrisDatasetPayload.self, json)
        guard !payload.points.isEmpty,
              payload.points.count == payload.targets.count,
              !payload.featureNames.isEmpty, !payload.classNames.isEmpty,
              payload.points.allSatisfy({ $0.count == payload.featureNames.count && $0.allSatisfy(\.isFinite) }),
              payload.targets.allSatisfy({ payload.classNames.indices.contains($0) }) else {
            throw IrisKernelError.invalidPayload("Dataset contains invalid dimensions, values or labels")
        }
        return payload
    }

    func train(dataset: DatasetKind, classifier: ClassifierKind, useScaler: Bool) async throws -> TrainingResult {
        struct Request: Encodable { let dataset: String; let classifier: String; let useScaler: Bool; let id: String }
        let request = Request(dataset: dataset.pythonKey, classifier: classifier.pythonKey, useScaler: useScaler,
                              id: UUID().uuidString)
        let encoded = String(decoding: try JSONEncoder().encode(request), as: UTF8.self)
        let json: String = try await pool.invokeResult(
            module: "iris_kernel", function: "train_model", args: [encoded], worker: 0, timeout: 120)
        let result = try decode(TrainingResult.self, json)
        guard result.id == request.id, result.dataset == request.dataset, result.classifier == request.classifier else {
            throw IrisKernelError.invalidPayload("Training returned a different request identity")
        }
        return result
    }

    func predict(modelID: String, values: [Double]) async throws -> ExperimentPrediction {
        let json: String = try await pool.invokeResult(
            module: "iris_kernel", function: "predict_sample", args: [modelID, values], worker: 0, timeout: 10)
        let result = try decode(ExperimentPrediction.self, json)
        guard result.modelID == modelID else { throw IrisKernelError.invalidPayload("Prediction model changed") }
        return result
    }

    func shutdown() async {
        guard !stopped else { return }
        stopped = true
        await pool.shutdown()
    }

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }
}
