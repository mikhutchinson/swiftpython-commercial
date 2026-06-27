import Foundation
import SwiftPythonRuntime
import SwiftPythonKit

// MARK: - Candidate

enum RunStatus: Equatable {
    case idle
    case training
    case done
}

/// One scikit-learn classifier raced in the bake-off, pinned to one worker.
struct Candidate: Identifiable {
    let id = UUID()
    let name: String
    let estimator: EstimatorKind
    let workerIndex: Int
    let colorIndex: Int
    /// The literal Swift the app runs for this candidate — shown on screen.
    let code: String

    var status: RunStatus = .idle
    var accuracy: Double? = nil
    var elapsedMs: Int? = nil
}

/// Which generated `pool.sklearn` estimator a candidate drives. Each case knows how
/// to train + score + predict through the canonical pool surface.
enum EstimatorKind {
    case randomForest
    case gradientBoosting
    case logisticRegression
    case kNeighbors

    /// Train + score on the pinned worker, returning (testAccuracy, testPredictions).
    func trainScorePredict(
        pool: PythonProcessPool,
        worker: Int,
        xTrain: [[Double]], yTrain: [Int],
        xTest: [[Double]], yTest: [Int]
    ) async throws -> (accuracy: Double, predictions: [Int]) {
        switch self {
        case .randomForest:
            let m = try await pool.sklearn.ensemble.RandomForestClassifier(n_estimators: 300, random_state: 0, worker: worker)
            let fitted = try await m.fit(X: xTrain, y: yTrain, worker: worker)
            let acc = try await fitted.score(X: xTest, y: yTest, worker: worker)
            let preds = try await Self.predictList(pool: pool, handle: fitted, x: xTest, worker: worker)
            return (acc, preds)
        case .gradientBoosting:
            let m = try await pool.sklearn.ensemble.GradientBoostingClassifier(n_estimators: 200, random_state: 0, worker: worker)
            let fitted = try await m.fit(X: xTrain, y: yTrain, worker: worker)
            let acc = try await fitted.score(X: xTest, y: yTest, worker: worker)
            let preds = try await Self.predictList(pool: pool, handle: fitted, x: xTest, worker: worker)
            return (acc, preds)
        case .logisticRegression:
            let m = try await pool.sklearn.linearmodel.LogisticRegression(random_state: 0, max_iter: 2000, worker: worker)
            let fitted = try await m.fit(X: xTrain, y: yTrain, worker: worker)
            let acc = try await fitted.score(X: xTest, y: yTest, worker: worker)
            let preds = try await Self.predictList(pool: pool, handle: fitted, x: xTest, worker: worker)
            return (acc, preds)
        case .kNeighbors:
            let m = try await pool.sklearn.neighbors.KNeighborsClassifier(n_neighbors: 7, worker: worker)
            let fitted = try await m.fit(X: xTrain, y: yTrain, worker: worker)
            let acc = try await fitted.score(X: xTest, y: yTest, worker: worker)
            let preds = try await Self.predictList(pool: pool, handle: fitted, x: xTest, worker: worker)
            return (acc, preds)
        }
    }

    private static func predictList(
        pool: PythonProcessPool,
        handle: some HandleConvertible,
        x: [[Double]],
        worker: Int
    ) async throws -> [Int] {
        let predsHandle = try await pool.methodOwned(handle: handle, name: "predict", args: [.python(x)], worker: worker)
        return try await pool.methodResult(handle: predsHandle, name: "tolist", worker: worker)
    }
}

// MARK: - Worker lane

struct WorkerLane: Identifiable {
    let id: Int        // worker index
    var label: String = "idle"
    var status: RunStatus = .idle
    var colorIndex: Int? = nil
}

// MARK: - Console

struct LogLine: Identifiable {
    let id = UUID()
    let text: String
    let kind: Kind
    enum Kind { case prompt, info, result, dim }
}

// MARK: - Confusion matrix

struct ConfusionMatrix {
    let labels: [String]
    let counts: [[Int]]   // counts[true][pred]
}

// MARK: - Dataset

struct DatasetInfo {
    let samples: Int
    let features: Int
    let classes: Int
    let trainCount: Int
    let testCount: Int
}
