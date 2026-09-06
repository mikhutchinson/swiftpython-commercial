import Foundation

enum DatasetKind: String, CaseIterable, Identifiable, Sendable {
    case iris = "Iris"
    case wine = "Wine"
    case breastCancer = "Breast Cancer"
    var id: String { rawValue }
    var pythonKey: String {
        switch self { case .iris: "iris"; case .wine: "wine"; case .breastCancer: "breast_cancer" }
    }
    var detail: String {
        switch self {
        case .iris: "150 flowers · 4 measurements"
        case .wine: "178 wines · 13 measurements"
        case .breastCancer: "569 samples · 30 measurements"
        }
    }
    var symbol: String {
        switch self { case .iris: "leaf"; case .wine: "wineglass"; case .breastCancer: "waveform.path.ecg" }
    }
}

enum ClassifierKind: String, CaseIterable, Identifiable, Sendable {
    case logisticRegression = "Logistic Regression"
    case randomForest = "Random Forest"
    case kNeighbors = "K-Nearest Neighbors"
    var id: String { rawValue }
    var pythonKey: String {
        switch self {
        case .logisticRegression: "logistic_regression"
        case .randomForest: "random_forest"
        case .kNeighbors: "k_neighbors"
        }
    }
}

struct IrisDatasetPayload: Decodable, Sendable {
    let featureNames: [String]
    let classNames: [String]
    let points: [[Double]]
    let targets: [Int]
    var samples: [DataPoint] {
        points.enumerated().map { DataPoint(id: $0.offset, values: $0.element, classID: targets[$0.offset]) }
    }
}

struct DataPoint: Identifiable, Sendable {
    let id: Int
    let values: [Double]
    let classID: Int
}

struct SamplePrediction: Codable, Sendable, Identifiable {
    let id: Int
    let actual: Int
    let predicted: Int
    let probabilities: [Double]
    var isMistake: Bool { actual != predicted }
    var confidence: Double { probabilities.max() ?? 0 }
}

struct TrainingResult: Decodable, Sendable, Identifiable {
    let id: String
    let dataset: String
    let classifier: String
    let useScaler: Bool
    let trainCount: Int
    let testCount: Int
    let testAccuracy: Double
    let cvAccuracyMean: Double
    let cvAccuracyStd: Double
    let confusionMatrix: [[Int]]
    let classificationReport: String
    let learningCurveTrainSizes: [Int]
    let learningCurveMeanTrainScores: [Double]
    let learningCurveMeanTestScores: [Double]
    let predictions: [SamplePrediction]
    let elapsedSeconds: Double
    var mistakes: [SamplePrediction] { predictions.filter(\.isMistake) }
}

struct ExperimentPrediction: Decodable, Sendable {
    let modelID: String
    let predicted: Int
    let probabilities: [Double]
}

enum IrisKernelError: LocalizedError {
    case missingResource(String)
    case invalidPayload(String)
    var errorDescription: String? {
        switch self {
        case .missingResource(let path): "Missing bundled resource: \(path). Build the app with scripts/build_app.sh."
        case .invalidPayload(let message): message
        }
    }
}
