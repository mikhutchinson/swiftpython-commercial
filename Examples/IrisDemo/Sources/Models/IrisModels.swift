import Foundation

// MARK: - Dataset kind (sklearn.datasets)

enum DatasetKind: String, CaseIterable, Identifiable {
    case iris = "Iris"
    case wine = "Wine"
    case breastCancer = "Breast Cancer"

    var id: String { rawValue }
    var loaderDescription: String {
        switch self {
        case .iris: return "load_iris"
        case .wine: return "load_wine"
        case .breastCancer: return "load_breast_cancer"
        }
    }
}

// MARK: - Classifier choice (Sklearn APIs showcase)

enum ClassifierKind: String, CaseIterable, Identifiable {
    case logisticRegression = "Logistic Regression"
    case randomForest = "Random Forest"
    case kNeighbors = "K-Neighbors"

    var id: String { rawValue }
}

// MARK: - Generic data point (any number of features + class)

struct DataPoint: Identifiable {
    let id = UUID()
    let values: [Double]  // feature values in order
    let classId: Int
    func className(from names: [String]) -> String {
        guard classId >= 0, classId < names.count else { return "\(classId)" }
        return names[classId]
    }
    func value(at index: Int) -> Double {
        guard index >= 0, index < values.count else { return 0 }
        return values[index]
    }
}

// MARK: - Feature statistics

struct FeatureStat: Identifiable {
    let id = UUID()
    let name: String
    let mean: Double
    let std: Double
    let min: Double
    let max: Double
}

// MARK: - Class distribution (for Swift Charts)

struct ClassCount: Identifiable {
    let id = UUID()
    let className: String
    let count: Int
}

// MARK: - Training result (Sklearn metrics showcase)

struct TrainingResult: Identifiable {
    let id = UUID()
    let modelName: String
    let testAccuracy: Double
    let cvAccuracyMean: Double
    let cvAccuracyStd: Double
    let confusionMatrix: [[Int]]
    let classificationReport: String
    /// Learning curve: train sizes and mean train/test scores per size (from sklearn learning_curve).
    let learningCurveTrainSizes: [Int]
    let learningCurveMeanTrainScores: [Double]
    let learningCurveMeanTestScores: [Double]
}

// MARK: - Backward compatibility / convenience

extension DataPoint {
    /// Iris-specific: first 4 values are sepal length, sepal width, petal length, petal width
    var sepalLength: Double { value(at: 0) }
    var sepalWidth: Double { value(at: 1) }
    var petalLength: Double { value(at: 2) }
    var petalWidth: Double { value(at: 3) }
}
