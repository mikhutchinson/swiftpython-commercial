import SwiftUI
import Charts

struct TrainingDetails: View {
    let result: TrainingResult
    let classNames: [String]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("Training details").font(.title2.weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            Text("\(result.trainCount) training rows · \(result.testCount) held-out rows · scaling \(result.useScaler ? "on" : "off")")
                .foregroundStyle(.secondary)
            Text("Learning curve").font(.headline)
            Chart {
                ForEach(Array(result.learningCurveTrainSizes.enumerated()), id: \.offset) { index, count in
                    LineMark(x: .value("Training rows per fold", count), y: .value("Accuracy", result.learningCurveMeanTrainScores[index]))
                        .foregroundStyle(by: .value("Cohort", "Training"))
                    LineMark(x: .value("Training rows per fold", count), y: .value("Accuracy", result.learningCurveMeanTestScores[index]))
                        .foregroundStyle(by: .value("Cohort", "Validation"))
                }
            }.chartYScale(domain: 0...1).chartXAxisLabel("Training rows per fold")
                .chartYAxis { AxisMarks(format: FloatingPointFormatStyle<Double>.Percent.percent) }
                .frame(height: 190)
            Text("Five-fold validation accuracy: \(result.cvAccuracyMean.formatted(.percent.precision(.fractionLength(1)))) ± \((result.cvAccuracyStd * 100).formatted(.number.precision(.fractionLength(2)))) percentage points.")
                .font(.caption).foregroundStyle(.secondary)
            Text("Held-out confusion matrix").font(.headline)
            Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 8) {
                GridRow { Text("Actual ↓ / predicted →").foregroundStyle(.secondary); ForEach(classNames, id: \.self) { Text($0).fontWeight(.medium) } }
                ForEach(Array(result.confusionMatrix.enumerated()), id: \.offset) { row, values in
                    GridRow {
                        Text(classNames[row]).fontWeight(.medium)
                        ForEach(Array(values.enumerated()), id: \.offset) { column, count in
                            Text("\(count)").monospacedDigit().foregroundStyle(row != column && count > 0 ? .orange : .primary)
                        }
                    }
                }
            }.font(.callout)
            DisclosureGroup("Classification report") {
                ScrollView { Text(result.classificationReport).font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
                    .frame(maxHeight: 180)
            }
            Text("Each validation fold fits its own scaler. The held-out rows are excluded from cross-validation and the learning curve.")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(width: 680)
    }
}
