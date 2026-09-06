import SwiftUI

struct SampleInspector: View {
    @Bindable var model: IrisViewModel
    private let palette: [Color] = [.teal, .orange, .indigo]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Sample inspector").font(.headline)
                if let sample = model.selectedSample {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("#\(sample.id + 1)").font(.largeTitle.weight(.semibold)).monospacedDigit()
                        Label(model.className(sample.classID), systemImage: "circle.fill")
                            .foregroundStyle(palette[sample.classID % palette.count])
                        if let prediction = model.predictionsByID[sample.id] {
                            Text(prediction.isMistake ? "Held-out mistake" : "Correctly classified held-out sample")
                                .font(.callout).foregroundStyle(prediction.isMistake ? .orange : .secondary)
                            Text("Predicted \(model.className(prediction.predicted)) · \(prediction.confidence.formatted(.percent.precision(.fractionLength(1))))")
                                .font(.caption).foregroundStyle(.secondary)
                        } else if model.result != nil {
                            Text("Training row · excluded from the test score").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Divider()
                    VStack(alignment: .leading, spacing: 14) {
                        HStack {
                            Text("Experiment").font(.headline)
                            Spacer()
                            if model.isPredicting { ProgressView().controlSize(.mini) }
                        }
                        Text("Adjust the plotted features. The fitted model predicts again; the original sample and scores stay fixed.")
                            .font(.caption).foregroundStyle(.secondary)
                        featureControl(model.xFeature)
                        featureControl(model.yFeature)
                        Button("Reset to original sample") { model.resetExperiment() }.disabled(!model.experimentChanged)
                        if let prediction = model.experiment {
                            Text(model.className(prediction.predicted)).font(.title3.weight(.semibold))
                            ForEach(Array(prediction.probabilities.enumerated()), id: \.offset) { index, probability in
                                VStack(spacing: 4) {
                                    HStack {
                                        Text(model.className(index))
                                        Spacer()
                                        Text(probability.formatted(.percent.precision(.fractionLength(1)))).monospacedDigit()
                                    }.font(.caption)
                                    ProgressView(value: probability).tint(palette[index % palette.count])
                                }
                            }
                            Text(model.experimentChanged ? "Prediction for edited values" : "Prediction for original values")
                                .font(.caption).foregroundStyle(.secondary)
                        } else if !model.hasModel {
                            Text("Train a model to try different values.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Divider()
                    Text("Original measurements").font(.headline)
                    ForEach(Array((model.dataset?.featureNames ?? []).enumerated()), id: \.offset) { index, name in
                        HStack(alignment: .firstTextBaseline) {
                            Text(name).foregroundStyle(.secondary)
                            Spacer()
                            Text(sample.values[index].formatted(.number.precision(.fractionLength(0...3)))).monospacedDigit()
                        }.font(.caption)
                    }
                } else {
                    ContentUnavailableView("Select a sample", systemImage: "cursorarrow.rays", description: Text("Choose a chart point or table row to inspect its measurements and predictions."))
                }
            }.padding(20)
        }.background(Color(nsColor: .controlBackgroundColor))
    }

    private func featureControl(_ index: Int) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(model.dataset?.featureNames[index] ?? "Feature")
                Spacer()
                Text((model.experimentValues.indices.contains(index) ? model.experimentValues[index] : 0)
                    .formatted(.number.precision(.fractionLength(2)))).monospacedDigit()
            }.font(.caption)
            Slider(value: Binding(
                get: { model.experimentValues.indices.contains(index) ? model.experimentValues[index] : 0 },
                set: { model.editFeature(index, value: $0) }
            ), in: model.featureRange(index))
                .accessibilityLabel(model.dataset?.featureNames[index] ?? "Feature")
                .disabled(!model.hasModel)
        }
    }
}
