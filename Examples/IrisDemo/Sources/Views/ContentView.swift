import SwiftUI
import Charts

struct ContentView: View {
    @Bindable var model: IrisViewModel
    @State private var showTrainingDetails = false

    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 20) {
                Label("IRIS", systemImage: "leaf").font(.title2.weight(.semibold)).padding(.horizontal, 12)
                List(DatasetKind.allCases, selection: Binding<DatasetKind?>(
                    get: { model.selectedKind },
                    set: { kind in if let kind { Task { await model.loadDataset(kind) } } }
                )) { kind in
                    VStack(alignment: .leading, spacing: 5) {
                        Label(kind.rawValue, systemImage: kind.symbol).font(.headline)
                        Text(kind.detail).font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical, 8).tag(kind)
                }.listStyle(.sidebar).disabled(model.isTraining)
                VStack(alignment: .leading, spacing: 4) {
                    Text("SwiftPython").font(.caption.weight(.semibold))
                    Text(model.workerPID.map { "Python worker · \($0)" } ?? "Starting Python worker…")
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                }.padding(12)
            }.padding(.top, 20)
            .navigationSplitViewColumnWidth(min: 190, ideal: 205, max: 240)
        } detail: {
            HSplitView {
                workspace.frame(minWidth: 500)
                SampleInspector(model: model).frame(minWidth: 265, idealWidth: 285, maxWidth: 335)
            }
        }
        .navigationTitle("Iris")
        .frame(minWidth: 1050, minHeight: 730)
        .task { if model.dataset == nil && !model.isLoading { await model.loadDataset(model.selectedKind) } }
        .onChange(of: model.selectedSampleID) { _, _ in model.resetExperiment() }
        .onChange(of: model.mistakesOnly) { _, enabled in
            if enabled, !model.visibleSamples.contains(where: { $0.id == model.selectedSampleID }) {
                model.selectedSampleID = model.visibleSamples.first?.id
            }
        }
        .onChange(of: model.xFeature) { _, value in
            if value == model.yFeature, let count = model.dataset?.featureNames.count {
                model.yFeature = (value + 1) % count
            }
        }
        .onChange(of: model.yFeature) { _, value in
            if value == model.xFeature, let count = model.dataset?.featureNames.count {
                model.xFeature = (value + 1) % count
            }
        }
        .sheet(isPresented: $showTrainingDetails) {
            if let result = model.result { TrainingDetails(result: result, classNames: model.dataset?.classNames ?? []) }
        }
    }

    private var workspace: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                Text(model.selectedKind.rawValue).font(.largeTitle.weight(.semibold))
                Spacer()
                Text(model.selectedKind.detail).font(.subheadline).foregroundStyle(.secondary)
            }
            HStack(spacing: 14) {
                Picker("Model", selection: $model.selectedClassifier) {
                    ForEach(ClassifierKind.allCases) { Text($0.rawValue).tag($0) }
                }.labelsHidden().frame(maxWidth: 220).accessibilityLabel("Classifier")
                Toggle("Scale features", isOn: $model.useScaler).toggleStyle(.checkbox)
                Spacer(minLength: 0)
                Button { Task { await model.trainModel() } } label: {
                    if model.isTraining { ProgressView().controlSize(.small) } else { Text("Train model") }
                }.buttonStyle(.borderedProminent).keyboardShortcut("r", modifiers: .command)
            }.disabled(model.dataset == nil || model.isLoading || model.isTraining)

            if let error = model.error {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.triangle").foregroundStyle(.orange)
                    Text(error).font(.callout).textSelection(.enabled)
                    Spacer()
                    Button("Restart worker") { Task { await model.restart() } }
                }.padding(10).background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
            if model.isLoading {
                Spacer()
                ProgressView("Loading \(model.selectedKind.rawValue)…").frame(maxWidth: .infinity)
                Spacer()
            } else if model.dataset != nil {
                IrisScatterPlot(model: model).frame(minHeight: 270, idealHeight: 300)
                if let result = model.result {
                    resultSummary(result)
                } else {
                    Text("Select a sample to inspect it. Train a model to see predictions and held-out mistakes.")
                        .font(.callout).foregroundStyle(.secondary)
                }
                HStack {
                    Text("Samples").font(.headline)
                    Text("\(model.visibleSamples.count)").foregroundStyle(.secondary).monospacedDigit()
                    Spacer()
                    Toggle("Mistakes only", isOn: $model.mistakesOnly)
                        .toggleStyle(.checkbox).disabled(model.result == nil || model.isTraining)
                }
                sampleTable.frame(minHeight: 145, idealHeight: 210)
                Text("A fixed 75% training / 25% test split is shared across models. Cross-validation uses training rows only.")
                    .font(.caption).foregroundStyle(.secondary)
            } else { Spacer() }
        }.padding(20)
    }

    private func resultSummary(_ result: TrainingResult) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 25) {
                metric("Held-out accuracy", result.testAccuracy.formatted(.percent.precision(.fractionLength(1))))
                metric("5-fold CV", result.cvAccuracyMean.formatted(.percent.precision(.fractionLength(1))))
                metric("Mistakes", "\(result.mistakes.count) / \(result.testCount)")
                Spacer(minLength: 0)
                Button("Training details") { showTrainingDetails = true }
            }
            Text("\(ClassifierKind.allCases.first { $0.pythonKey == result.classifier }?.rawValue ?? result.classifier) · scaling \(result.useScaler ? "on" : "off") · \(result.trainCount) training rows · \(result.elapsedSeconds.formatted(.number.precision(.fractionLength(2)))) s")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(12).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }

    private func metric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).monospacedDigit()
        }
    }

    private var sampleTable: some View {
        let predictions = model.predictionsByID
        return Table(model.visibleSamples, selection: $model.selectedSampleID) {
            TableColumn("Sample") { Text("#\($0.id + 1)").monospacedDigit() }.width(60)
            TableColumn("Actual") { Text(model.className($0.classID)) }
            TableColumn("Prediction") { point in
                Text(predictions[point.id].map { model.className($0.predicted) } ?? "—")
            }
            TableColumn("Evaluation") { point in
                if let prediction = predictions[point.id] {
                    Label(prediction.isMistake ? "Mistake" : "Correct", systemImage: prediction.isMistake ? "xmark.circle" : "checkmark.circle")
                        .foregroundStyle(prediction.isMistake ? Color.orange : Color.secondary)
                } else { Text(model.result == nil ? "Not trained" : "Training row").foregroundStyle(.secondary) }
            }.width(min: 95, ideal: 115)
        }.overlay {
            if model.mistakesOnly && model.visibleSamples.isEmpty {
                ContentUnavailableView("No held-out mistakes", systemImage: "checkmark.circle", description: Text("Every sample in this test split was classified correctly."))
            }
        }
    }
}
