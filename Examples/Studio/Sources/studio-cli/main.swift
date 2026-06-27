// studio-cli — the terminal face of SwiftPython Studio.
//
// Races several scikit-learn classifiers across Python worker processes, driven
// entirely from Swift through the canonical `pool.<module>` surface. This is the
// "behind the curtain" view: same engine as the app, pure terminal output.

import Foundation
import SwiftPythonRuntime
import SwiftPythonKit

// MARK: - ANSI

enum A {
    static let reset = "\u{001B}[0m"
    static let bold = "\u{001B}[1m"
    static let dim = "\u{001B}[2m"
    static let orange = "\u{001B}[38;5;208m"
    static let blue = "\u{001B}[38;5;75m"
    static let green = "\u{001B}[38;5;78m"
    static let purple = "\u{001B}[38;5;141m"
    static let grey = "\u{001B}[38;5;245m"
}
let modelColor = [A.orange, A.blue, A.purple, A.green]

func bar(_ value: Double, width: Int = 28) -> String {
    let filled = Int((value * Double(width)).rounded())
    return String(repeating: "█", count: max(0, filled)) + String(repeating: "·", count: max(0, width - filled))
}

// MARK: - Deterministic dataset (SplitMix64)

struct SplitMix64 {
    var state: UInt64
    mutating func next() -> UInt64 {
        state &+= 0x9E3779B97F4A7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9
        z = (z ^ (z >> 27)) &* 0x94D049BB133111EB
        return z ^ (z >> 31)
    }
    mutating func unit() -> Double { Double(next() >> 11) * (1.0 / 9007199254740992.0) }
    mutating func gauss() -> Double {
        let u1 = max(unit(), 1e-12), u2 = unit()
        return (-2.0 * log(u1)).squareRoot() * cos(2.0 * .pi * u2)
    }
}

func makeDataset(samples: Int, features: Int, classes: Int, seed: UInt64)
    -> ([[Double]], [Int], [[Double]], [Int]) {
    var rng = SplitMix64(state: seed)
    var centroids: [[Double]] = []
    for c in 0..<classes {
        centroids.append((0..<features).map { f in Double((c + 1) * 6) * (f % 2 == 0 ? 1.0 : -0.6) + rng.gauss() * 0.5 })
    }
    var X: [[Double]] = []; var y: [Int] = []
    for i in 0..<samples {
        let c = i % classes
        X.append((0..<features).map { f in centroids[c][f] + rng.gauss() * 5.0 }); y.append(c)
    }
    var order = Array(0..<samples)
    for i in stride(from: samples - 1, to: 0, by: -1) { order.swapAt(i, Int(rng.next() % UInt64(i + 1))) }
    let split = samples * 4 / 5
    let tr = order[..<split], te = order[split...]
    return (tr.map { X[$0] }, tr.map { y[$0] }, te.map { X[$0] }, te.map { y[$0] })
}

// MARK: - Candidates

struct Spec { let name: String; let worker: Int; let color: String; let code: String
    let run: @Sendable (PythonProcessPool, [[Double]], [Int], [[Double]], [Int], Int) async throws -> Double }

let specs: [Spec] = [
    Spec(name: "Random Forest", worker: 0, color: modelColor[0],
         code: "pool.sklearn.ensemble.RandomForestClassifier(n_estimators: 300)") { p, xtr, ytr, xte, yte, w in
        let m = try await p.sklearn.ensemble.RandomForestClassifier(n_estimators: 300, random_state: 0, worker: w)
        return try await m.fit(X: xtr, y: ytr, worker: w).score(X: xte, y: yte, worker: w)
    },
    Spec(name: "Gradient Boosting", worker: 1, color: modelColor[1],
         code: "pool.sklearn.ensemble.GradientBoostingClassifier(n_estimators: 200)") { p, xtr, ytr, xte, yte, w in
        let m = try await p.sklearn.ensemble.GradientBoostingClassifier(n_estimators: 200, random_state: 0, worker: w)
        return try await m.fit(X: xtr, y: ytr, worker: w).score(X: xte, y: yte, worker: w)
    },
    Spec(name: "Logistic Regression", worker: 2, color: modelColor[2],
         code: "pool.sklearn.linearmodel.LogisticRegression(max_iter: 2000)") { p, xtr, ytr, xte, yte, w in
        let m = try await p.sklearn.linearmodel.LogisticRegression(random_state: 0, max_iter: 2000, worker: w)
        return try await m.fit(X: xtr, y: ytr, worker: w).score(X: xte, y: yte, worker: w)
    },
    Spec(name: "K-Neighbors", worker: 3, color: modelColor[3],
         code: "pool.sklearn.neighbors.KNeighborsClassifier(n_neighbors: 7)") { p, xtr, ytr, xte, yte, w in
        let m = try await p.sklearn.neighbors.KNeighborsClassifier(n_neighbors: 7, worker: w)
        return try await m.fit(X: xtr, y: ytr, worker: w).score(X: xte, y: yte, worker: w)
    },
]

func workerPath() -> String? {
    if let env = ProcessInfo.processInfo.environment["SWIFTPYTHON_WORKER_PATH"], !env.isEmpty { return env }
    return nil
}

// MARK: - Run

func runStudio() async {
        print("")
        print("  \(A.bold)\(A.orange)SwiftPython Studio\(A.reset) \(A.grey)· studio-cli\(A.reset)")
        print("  \(A.grey)Swift → scikit-learn, across real Python processes.\(A.reset)")
        print("")

        let (xtr, ytr, xte, yte) = makeDataset(samples: 1500, features: 12, classes: 3, seed: 0xC0FFEE)
        print("  \(A.dim)dataset built in Swift:\(A.reset) 1500×12, 3 classes  →  train \(ytr.count) / test \(yte.count)")

        do {
            StudioRuntime.configureBundledPythonIfPresent()
            let wall = Date()
            print("  \(A.orange)›\(A.reset) let pool = try await PythonProcessPool(workers: 4)")
            let pool = try await PythonProcessPool(workers: 4, workerExecutablePath: workerPath())
            print("  \(A.green)✓\(A.reset) 4 Python workers spawned")
            print("  \(A.dim)racing \(specs.count) models in parallel — each pinned to its own worker…\(A.reset)\n")

            let snapTr = xtr, snapYtr = ytr, snapTe = xte, snapYte = yte
            var results: [(name: String, color: String, acc: Double, ms: Int)] = []

            try await withThrowingTaskGroup(of: (Int, Double, Int).self) { group in
                for (i, spec) in specs.enumerated() {
                    group.addTask {
                        let t0 = Date()
                        let acc = try await spec.run(pool, snapTr, snapYtr, snapTe, snapYte, spec.worker)
                        return (i, acc, Int(Date().timeIntervalSince(t0) * 1000))
                    }
                }
                for try await (i, acc, ms) in group {
                    let s = specs[i]
                    print("  \(s.color)✓\(A.reset) [w\(s.worker)] \(s.name) → \(A.bold)\(String(format: "%.3f", acc))\(A.reset) \(A.dim)(\(ms) ms)\(A.reset)")
                    results.append((s.name, s.color, acc, ms))
                }
            }

            print("")
            for r in results.sorted(by: { $0.acc > $1.acc }) {
                let name = r.name.padding(toLength: 20, withPad: " ", startingAt: 0)
                print("  \(r.color)\(name)\(A.reset) \(r.color)\(bar(r.acc))\(A.reset) \(String(format: "%.3f", r.acc))")
            }
            if let best = results.max(by: { $0.acc < $1.acc }) {
                print("\n  \(A.green)\(A.bold)winner:\(A.reset) \(best.name) \(String(format: "(%.3f)", best.acc))")
            }
            print("  \(A.dim)total \(Int(Date().timeIntervalSince(wall) * 1000)) ms · handles auto-released\(A.reset)\n")
            await pool.shutdown()
            fflush(nil)
            exit(0)
        } catch {
            print("  \(A.bold)error:\(A.reset) \(error)")
            fflush(nil)
            exit(1)
        }
}

await runStudio()
