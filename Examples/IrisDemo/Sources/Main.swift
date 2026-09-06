import AppKit
import Foundation

@main
struct IrisMain {
    @MainActor
    static func main() async {
        if CommandLine.arguments.count == 1 {
            NSApplication.shared.setActivationPolicy(.regular)
            IrisDemoApp.main()
            return
        }
        do {
            guard CommandLine.arguments.count == 3,
                  CommandLine.arguments[1] == "--smoke" else {
                throw IrisKernelError.invalidPayload("Usage: IRIS [--smoke /path/to/receipt.json]")
            }
            let service = try await IrisKernel()
            do {
                var results: [[String: Any]] = []
                for dataset in DatasetKind.allCases {
                    let data = try await service.loadDataset(dataset)
                    guard !data.points.isEmpty, data.points.count == data.targets.count else {
                        throw IrisKernelError.invalidPayload("Dataset shape mismatch")
                    }
                    for classifier in ClassifierKind.allCases {
                        let trained = try await service.train(
                            dataset: dataset, classifier: classifier, useScaler: true)
                        guard trained.testAccuracy.isFinite,
                              (0...1).contains(trained.testAccuracy),
                              trained.cvAccuracyMean.isFinite,
                              (0...1).contains(trained.cvAccuracyMean),
                              trained.confusionMatrix.count == data.classNames.count else {
                            throw IrisKernelError.invalidPayload("Invalid training result")
                        }
                        let sample = trained.predictions[0]
                        let predicted = try await service.predict(modelID: trained.id, values: data.points[sample.id])
                        guard predicted.predicted == sample.predicted,
                              trained.trainCount + trained.testCount == data.points.count,
                              Set(trained.predictions.map(\.id)).count == trained.testCount else {
                            throw IrisKernelError.invalidPayload("Retained model prediction or test cohort mismatch")
                        }
                        results.append([
                            "dataset": dataset.pythonKey,
                            "classifier": classifier.pythonKey,
                            "rows": data.points.count,
                            "testAccuracy": trained.testAccuracy,
                            "cvAccuracyMean": trained.cvAccuracyMean,
                        ])
                        print("Verified \(dataset.pythonKey) / \(classifier.pythonKey): \(trained.testAccuracy)")
                    }
                }
                let receipt = try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
                try receipt.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
                await service.shutdown()
                guard kill(service.workerPID, 0) == -1 && errno == ESRCH else {
                    throw IrisKernelError.invalidPayload("Iris worker was not reaped")
                }
                print("Verified one persistent worker for all datasets/models; owned worker reaped")
                fflush(nil)
                _Exit(0)
            } catch {
                await service.shutdown()
                throw error
            }
        } catch {
            FileHandle.standardError.write(Data("IRIS: \(error)\n".utf8))
            fflush(nil)
            _Exit(1)
        }
    }
}
