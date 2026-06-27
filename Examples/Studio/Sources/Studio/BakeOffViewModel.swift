import Foundation
import SwiftUI
import SwiftPythonRuntime
import SwiftPythonKit

@MainActor
final class BakeOffViewModel: ObservableObject {
    enum Phase: Equatable { case idle, running, done }

    @Published var phase: Phase = .idle
    @Published var candidates: [Candidate]
    @Published var workers: [WorkerLane]
    @Published var log: [LogLine] = []
    @Published var confusion: ConfusionMatrix?
    @Published var winnerName: String?
    @Published var elapsedMs: Int = 0

    let dataset: DatasetInfo
    private let classNames = ["Setosa", "Vinifera", "Acuminata"]
    private let workerCount = 4
    private var pool: PythonProcessPool?

    // Cached dataset (built once, deterministic).
    private let xTrain: [[Double]]
    private let yTrain: [Int]
    private let xTest: [[Double]]
    private let yTest: [Int]

    init() {
        let (xtr, ytr, xte, yte) = Self.makeDataset(samples: 1500, features: 12, classes: 3, seed: 0xC0FFEE)
        self.xTrain = xtr; self.yTrain = ytr; self.xTest = xte; self.yTest = yte
        self.dataset = DatasetInfo(samples: 1500, features: 12, classes: 3, trainCount: ytr.count, testCount: yte.count)

        self.candidates = [
            Candidate(name: "Random Forest", estimator: .randomForest, workerIndex: 0, colorIndex: 0,
                      code: "pool.sklearn.ensemble\n  .RandomForestClassifier(n_estimators: 300)"),
            Candidate(name: "Gradient Boosting", estimator: .gradientBoosting, workerIndex: 1, colorIndex: 1,
                      code: "pool.sklearn.ensemble\n  .GradientBoostingClassifier(n_estimators: 200)"),
            Candidate(name: "Logistic Regression", estimator: .logisticRegression, workerIndex: 2, colorIndex: 2,
                      code: "pool.sklearn.linearmodel\n  .LogisticRegression(max_iter: 2000)"),
            Candidate(name: "K-Neighbors", estimator: .kNeighbors, workerIndex: 3, colorIndex: 3,
                      code: "pool.sklearn.neighbors\n  .KNeighborsClassifier(n_neighbors: 7)"),
        ]
        self.workers = (0..<workerCount).map { WorkerLane(id: $0) }
    }

    func run() {
        guard phase != .running else { return }
        Task { await runBakeOff() }
    }

    func runBakeOff() async {
        reset()
        phase = .running
        let wallStart = Date()

        do {
            StudioRuntime.configureBundledPythonIfPresent()
            emit("let pool = try await PythonProcessPool(workers: \(workerCount))", .prompt)
            let pool = try await PythonProcessPool(workers: workerCount, workerExecutablePath: Self.resolveWorkerPath())
            self.pool = pool
            emit("\(workerCount) Python workers spawned", .result)
            emit("dataset built in Swift: \(dataset.samples)×\(dataset.features), \(dataset.classes) classes "
                 + "→ train \(dataset.trainCount) / test \(dataset.testCount)", .info)
            emit("racing \(candidates.count) scikit-learn models across \(workerCount) processes…", .info)

            // Light every lane up at once — the models train in parallel.
            for i in candidates.indices {
                candidates[i].status = .training
                workers[i].status = .training
                workers[i].label = candidates[i].name
                workers[i].colorIndex = candidates[i].colorIndex
                emit("[w\(candidates[i].workerIndex)] \(candidates[i].name): fit(X, y) → score(X_test)", .prompt)
            }

            // Snapshot Sendable inputs for the concurrent tasks.
            let xtr = xTrain, ytr = yTrain, xte = xTest, yte = yTest
            let specs: [(idx: Int, worker: Int, est: EstimatorKind)] =
                candidates.enumerated().map { ($0.offset, $0.element.workerIndex, $0.element.estimator) }

            var predictionsByIndex: [Int: [Int]] = [:]

            try await withThrowingTaskGroup(of: (Int, Double, [Int], Int).self) { group in
                for spec in specs {
                    group.addTask {
                        let t0 = Date()
                        let (acc, preds) = try await spec.est.trainScorePredict(
                            pool: pool, worker: spec.worker,
                            xTrain: xtr, yTrain: ytr, xTest: xte, yTest: yte
                        )
                        let ms = Int(Date().timeIntervalSince(t0) * 1000)
                        return (spec.idx, acc, preds, ms)
                    }
                }
                // Apply results as each model finishes — bars fill in live.
                for try await (idx, acc, preds, ms) in group {
                    candidates[idx].status = .done
                    candidates[idx].accuracy = acc
                    candidates[idx].elapsedMs = ms
                    workers[idx].status = .done
                    predictionsByIndex[idx] = preds
                    emit(String(format: "[w%d] %@ → accuracy %.3f  (%d ms)",
                                candidates[idx].workerIndex, candidates[idx].name, acc, ms), .result)
                }
            }

            // Winner + confusion matrix (computed in Swift from the winner's predictions).
            if let best = candidates.enumerated().max(by: { ($0.element.accuracy ?? 0) < ($1.element.accuracy ?? 0) }),
               let preds = predictionsByIndex[best.offset] {
                winnerName = best.element.name
                confusion = Self.confusionMatrix(truth: yTest, pred: preds, labels: classNames)
                emit(String(format: "winner: %@ (%.3f) — confusion matrix below",
                            best.element.name, best.element.accuracy ?? 0), .info)
            }

            elapsedMs = Int(Date().timeIntervalSince(wallStart) * 1000)
            emit("done in \(elapsedMs) ms — handles auto-released on scope exit", .dim)
            phase = .done
            await pool.shutdown()
            self.pool = nil
        } catch {
            emit("error: \(error)", .result)
            phase = .idle
            if let pool { await pool.shutdown(); self.pool = nil }
        }
    }

    private func reset() {
        for i in candidates.indices {
            candidates[i].status = .idle
            candidates[i].accuracy = nil
            candidates[i].elapsedMs = nil
        }
        workers = (0..<workerCount).map { WorkerLane(id: $0) }
        log = []
        confusion = nil
        winnerName = nil
        elapsedMs = 0
    }

    private func emit(_ text: String, _ kind: LogLine.Kind) {
        log.append(LogLine(text: text, kind: kind))
    }

    // MARK: - Worker resolution

    private static func resolveWorkerPath() -> String? {
        let fm = FileManager.default
        if let env = ProcessInfo.processInfo.environment["SWIFTPYTHON_WORKER_PATH"], !env.isEmpty,
           fm.fileExists(atPath: env) { return env }
        if let dir = Bundle.main.executableURL?.deletingLastPathComponent() {
            let bundled = dir.appendingPathComponent("SwiftPythonWorker").path
            if fm.fileExists(atPath: bundled) { return bundled }
        }
        return nil  // fall back to runtime auto-discovery
    }

    // MARK: - Deterministic dataset

    /// Gaussian clusters per class → a real multi-class classification problem with
    /// enough overlap to spread model accuracy. Fully deterministic for reproducible demos.
    private static func makeDataset(samples: Int, features: Int, classes: Int, seed: UInt64)
        -> ([[Double]], [Int], [[Double]], [Int]) {
        var rng = SplitMix64(seed: seed)
        // Class centroids spaced across feature space.
        var centroids: [[Double]] = []
        for c in 0..<classes {
            centroids.append((0..<features).map { f in
                Double((c + 1) * 6) * (f % 2 == 0 ? 1.0 : -0.6) + rng.nextGaussian() * 0.5
            })
        }
        var X: [[Double]] = []
        var y: [Int] = []
        for i in 0..<samples {
            let c = i % classes
            let row = (0..<features).map { f in centroids[c][f] + rng.nextGaussian() * 5.0 }
            X.append(row); y.append(c)
        }
        // Deterministic shuffle then 80/20 split.
        var order = Array(0..<samples)
        for i in stride(from: samples - 1, to: 0, by: -1) {
            let j = Int(rng.next() % UInt64(i + 1))
            order.swapAt(i, j)
        }
        let split = (samples * 4) / 5
        let trainIdx = order[..<split], testIdx = order[split...]
        return (trainIdx.map { X[$0] }, trainIdx.map { y[$0] },
                testIdx.map { X[$0] }, testIdx.map { y[$0] })
    }

    private static func confusionMatrix(truth: [Int], pred: [Int], labels: [String]) -> ConfusionMatrix {
        let n = labels.count
        var counts = Array(repeating: Array(repeating: 0, count: n), count: n)
        for (t, p) in zip(truth, pred) where t >= 0 && t < n && p >= 0 && p < n {
            counts[t][p] += 1
        }
        return ConfusionMatrix(labels: labels, counts: counts)
    }
}

/// Tiny deterministic PRNG (SplitMix64) — no Foundation randomness, fully reproducible.
private struct SplitMix64 {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E3779B97F4A7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9
        z = (z ^ (z >> 27)) &* 0x94D049BB133111EB
        return z ^ (z >> 31)
    }
    mutating func nextUnit() -> Double { Double(next() >> 11) * (1.0 / 9007199254740992.0) }
    mutating func nextGaussian() -> Double {
        let u1 = max(nextUnit(), 1e-12), u2 = nextUnit()
        return (-2.0 * log(u1)).squareRoot() * cos(2.0 * .pi * u2)
    }
}
