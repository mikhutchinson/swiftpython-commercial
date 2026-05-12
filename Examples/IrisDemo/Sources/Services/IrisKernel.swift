import Foundation
import SwiftPythonRuntime

enum IrisKernel {
    static func loadDataset(_ kind: DatasetKind) async throws -> IrisDatasetPayload {
        let json = try await call(function: "load_dataset", arguments: [kind.pythonKey])
        return try decode(IrisDatasetPayload.self, from: json)
    }

    static func train(
        dataset: DatasetKind,
        classifier: ClassifierKind,
        useScaler: Bool
    ) async throws -> TrainingResult {
        let payload: [String: Any] = [
            "dataset": dataset.pythonKey,
            "classifier": classifier.pythonKey,
            "classifierName": classifier.rawValue,
            "useScaler": useScaler,
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        guard let jsonPayload = String(data: data, encoding: .utf8) else {
            throw IrisKernelError.invalidPayload("Could not encode training request")
        }

        let json = try await call(function: "train_model", arguments: [jsonPayload])
        let payloadResult = try decode(IrisTrainingPayload.self, from: json)
        return TrainingResult(
            modelName: payloadResult.modelName,
            testAccuracy: payloadResult.testAccuracy,
            cvAccuracyMean: payloadResult.cvAccuracyMean,
            cvAccuracyStd: payloadResult.cvAccuracyStd,
            confusionMatrix: payloadResult.confusionMatrix,
            classificationReport: payloadResult.classificationReport,
            learningCurveTrainSizes: payloadResult.learningCurveTrainSizes,
            learningCurveMeanTrainScores: payloadResult.learningCurveMeanTrainScores,
            learningCurveMeanTestScores: payloadResult.learningCurveMeanTestScores
        )
    }

    private static func call(
        function: String,
        arguments: [any PythonConvertible]
    ) async throws -> String {
        let source = try loadResource(named: "iris_kernel", ext: "py", subdir: "Python")
        return try await Python.run {
            let builtins = try Python.import("builtins")
            let namespace = try builtins.getAttribute("dict").call()
            let execFunction = try builtins.getAttribute("exec")
            _ = try execFunction.call(args: [try source.toPythonObject(), namespace])
            let callable = try namespace[pyKey: function]
            let pyArgs = try arguments.map { try $0.toPythonObject() }
            let result = try callable.call(args: pyArgs)
            return try String(pythonObject: result)
        }
    }

    private static func decode<T: Decodable>(_ type: T.Type, from json: String) throws -> T {
        guard let data = json.data(using: .utf8) else {
            throw IrisKernelError.invalidPayload("Python returned non-UTF8 JSON")
        }
        return try JSONDecoder().decode(type, from: data)
    }

    private static func loadResource(named: String, ext: String, subdir: String) throws -> String {
        let url = Bundle.module.url(forResource: named, withExtension: ext, subdirectory: subdir)
            ?? Bundle.module.url(forResource: named, withExtension: ext)
        guard let url else {
            throw IrisKernelError.missingResource("\(subdir)/\(named).\(ext)")
        }
        return try String(contentsOf: url, encoding: .utf8)
    }
}

struct IrisDatasetPayload: Decodable {
    let featureNames: [String]
    let classNames: [String]
    let points: [[Double]]
    let targets: [Int]
}

private struct IrisTrainingPayload: Decodable {
    let modelName: String
    let testAccuracy: Double
    let cvAccuracyMean: Double
    let cvAccuracyStd: Double
    let confusionMatrix: [[Int]]
    let classificationReport: String
    let learningCurveTrainSizes: [Int]
    let learningCurveMeanTrainScores: [Double]
    let learningCurveMeanTestScores: [Double]
}

enum IrisKernelError: LocalizedError {
    case missingResource(String)
    case invalidPayload(String)

    var errorDescription: String? {
        switch self {
        case .missingResource(let path):
            return "Missing bundled Python resource: \(path)"
        case .invalidPayload(let message):
            return message
        }
    }
}

extension DatasetKind {
    var pythonKey: String {
        switch self {
        case .iris:
            return "iris"
        case .wine:
            return "wine"
        case .breastCancer:
            return "breast_cancer"
        }
    }
}

extension ClassifierKind {
    var pythonKey: String {
        switch self {
        case .logisticRegression:
            return "logistic_regression"
        case .randomForest:
            return "random_forest"
        case .kNeighbors:
            return "k_neighbors"
        }
    }
}
