import SwiftUI
import Charts

struct ContentView: View {
    @StateObject private var viewModel = IrisViewModel()

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.06, green: 0.08, blue: 0.12),
                    Color(red: 0.08, green: 0.06, blue: 0.14)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            if viewModel.isLoading {
                loadingView
            } else if let error = viewModel.error {
                errorView(error)
            } else {
                chartContent
            }
        }
        .preferredColorScheme(.dark)
        .onAppear { Task { await viewModel.loadDataset() } }
    }

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
                .tint(.mint)
            Text("Loading \(viewModel.selectedKind.rawValue) (\(viewModel.selectedKind.loaderDescription))...")
                .font(.subheadline)
                .foregroundColor(.gray)
        }
    }

    private func errorView(_ message: String) -> some View {
        VStack(spacing: 20) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 48))
                .foregroundStyle(.orange)
            Text("Failed to load dataset")
                .font(.title2.bold())
                .foregroundColor(.white)
            Text(message)
                .font(.subheadline)
                .foregroundColor(.gray)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
            Button("Retry") {
                Task { await viewModel.loadDataset() }
            }
            .buttonStyle(.borderedProminent)
            .tint(.mint)
        }
    }

    private var chartContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                datasetPicker
                HStack(alignment: .top, spacing: 20) {
                    scatterCard
                    classDistributionCard
                }
                HStack(alignment: .top, spacing: 20) {
                    featureMeansCard
                    mlCard
                }
                statsCard
            }
            .padding(24)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "leaf.fill")
                    .font(.title)
                    .foregroundStyle(.mint)
                Text("Sklearn Datasets + Swift Charts")
                    .font(.largeTitle.bold())
                    .foregroundColor(.white)
            }
            Text("Iris • Wine • Breast Cancer • train_test_split • cross_val_score • metrics • SwiftPython")
                .font(.subheadline)
                .foregroundColor(.gray)
        }
    }

    private var datasetPicker: some View {
        HStack(spacing: 12) {
            Text("Dataset")
                .font(.subheadline.bold())
                .foregroundColor(.gray)
            Picker("Dataset", selection: $viewModel.selectedKind) {
                ForEach(DatasetKind.allCases) { kind in
                    Text(kind.rawValue).tag(kind)
                }
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 400)
            .onChange(of: viewModel.selectedKind) { _, newKind in
                Task { await viewModel.load(kind: newKind) }
            }
        }
    }

    private var scatterCard: some View {
        card(title: "Feature 1 vs Feature 2 (by class)", icon: "point.topleft.down.to.point.bottomright.curvepath.fill") {
            if !viewModel.points.isEmpty, viewModel.featureNames.count >= 2 {
                Chart(viewModel.points) { point in
                    PointMark(
                        x: .value(viewModel.featureNames[0], point.value(at: 0)),
                        y: .value(viewModel.featureNames[1], point.value(at: 1))
                    )
                    .foregroundStyle(by: .value("Class", point.className(from: viewModel.classNames)))
                    .symbolSize(44)
                }
                .chartLegend(position: .trailing, spacing: 8)
                .chartForegroundStyleScale(range: [.green, .orange, .purple, .blue])
                .chartXAxis { axisMarks(.gray) }
                .chartYAxis { axisMarks(.gray) }
                .frame(height: 260)
            }
        }
        .frame(maxWidth: .infinity)
    }

    private var classDistributionCard: some View {
        card(title: "Class distribution", icon: "chart.pie.fill") {
            if !viewModel.classDistribution.isEmpty {
                Chart(viewModel.classDistribution) { item in
                    SectorMark(
                        angle: .value("Count", item.count),
                        innerRadius: .ratio(0.55),
                        angularInset: 2
                    )
                    .foregroundStyle(by: .value("Class", item.className))
                    .cornerRadius(4)
                }
                .chartLegend(position: .trailing, spacing: 8)
                .chartForegroundStyleScale(range: [.green, .orange, .purple, .blue])
                .frame(height: 260)
            }
        }
        .frame(maxWidth: 320)
    }

    private var featureMeansCard: some View {
        card(title: "Feature means", icon: "chart.bar.fill") {
            if !viewModel.featureStats.isEmpty {
                let displayStats = Array(viewModel.featureStats.prefix(8))
                Chart(displayStats) { stat in
                    BarMark(
                        x: .value("Mean", stat.mean),
                        y: .value("Feature", stat.name)
                    )
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.mint.opacity(0.9), .green.opacity(0.6)],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .cornerRadius(6)
                }
                .chartXAxis { axisMarks(.gray) }
                .chartYAxis { axisMarks(.gray) }
                .frame(height: 220)
            }
        }
        .frame(maxWidth: 400)
    }

    private var mlCard: some View {
        card(title: "Classifiers & metrics", icon: "brain") {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Classifier")
                            .font(.caption)
                            .foregroundColor(.gray)
                        Picker("Classifier", selection: $viewModel.selectedClassifier) {
                            ForEach(ClassifierKind.allCases) { kind in
                                Text(kind.rawValue).tag(kind)
                            }
                        }
                        .pickerStyle(.menu)
                        .frame(maxWidth: 200)
                    }
                    Toggle(isOn: $viewModel.useScaler) {
                        Text("Scale features")
                            .font(.subheadline)
                            .foregroundColor(.gray)
                    }
                    .toggleStyle(.switch)
                    .frame(maxWidth: 160)
                }
                if viewModel.isTraining {
                    HStack(spacing: 8) {
                        ProgressView()
                            .scaleEffect(0.9)
                            .tint(.mint)
                        Text("Training (learning_curve + cross_val_score + fit + metrics)...")
                            .font(.subheadline)
                            .foregroundColor(.gray)
                    }
                } else if let result = viewModel.trainingResult {
                    HStack(alignment: .top, spacing: 20) {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(alignment: .firstTextBaseline, spacing: 16) {
                                HStack(spacing: 6) {
                                    Text("Test")
                                        .font(.caption)
                                        .foregroundColor(.gray)
                                    Text(String(format: "%.1f%%", result.testAccuracy * 100))
                                        .font(.headline.bold())
                                        .foregroundColor(.mint)
                                }
                                HStack(spacing: 6) {
                                    Text("CV (5-fold)")
                                        .font(.caption)
                                        .foregroundColor(.gray)
                                    Text(String(format: "%.1f ± %.2f%%", result.cvAccuracyMean * 100, result.cvAccuracyStd * 100))
                                        .font(.headline)
                                        .foregroundColor(.white.opacity(0.9))
                                }
                            }
                            Text("Classification report")
                                .font(.caption)
                                .foregroundColor(.gray)
                            ScrollView(.vertical, showsIndicators: true) {
                                Text(result.classificationReport)
                                    .font(.system(.caption, design: .monospaced))
                                    .foregroundColor(.white.opacity(0.85))
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 140)
                            if !result.confusionMatrix.isEmpty {
                                Text("Confusion matrix")
                                    .font(.subheadline.bold())
                                    .foregroundColor(.white.opacity(0.9))
                                confusionMatrixGrid(result.confusionMatrix, classNames: viewModel.classNames, prominent: true)
                            }
                        }
                        .frame(minWidth: 280, maxWidth: 400, alignment: .leading)
                        if !result.learningCurveTrainSizes.isEmpty {
                            learningCurveChart(result: result)
                                .frame(minWidth: 220, maxWidth: 320)
                        }
                    }
                } else {
                    Text("75% train / 25% test (stratified). Optional StandardScaler, cross_val_score, confusion_matrix, classification_report.")
                        .font(.caption)
                        .foregroundColor(.gray)
                }
                Button(action: { Task { await viewModel.trainModel() } }) {
                    Label("Train model", systemImage: "play.fill")
                        .font(.subheadline.bold())
                }
                .buttonStyle(.borderedProminent)
                .tint(.mint)
                .disabled(viewModel.isTraining || viewModel.points.isEmpty)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity)
    }

    private func learningCurveChart(result: TrainingResult) -> some View {
        let indices = Array(result.learningCurveTrainSizes.indices)
        return VStack(alignment: .leading, spacing: 8) {
            Text("Learning curve")
                .font(.caption)
                .foregroundColor(.gray)
            Chart {
                ForEach(indices, id: \.self) { i in
                    LineMark(
                        x: .value("Train size", result.learningCurveTrainSizes[i]),
                        y: .value("Score", result.learningCurveMeanTrainScores[i])
                    )
                    .foregroundStyle(by: .value("Series", "Train"))
                    .interpolationMethod(.catmullRom)
                }
                ForEach(indices, id: \.self) { i in
                    LineMark(
                        x: .value("Train size", result.learningCurveTrainSizes[i]),
                        y: .value("Score", result.learningCurveMeanTestScores[i])
                    )
                    .foregroundStyle(by: .value("Series", "Validation"))
                    .interpolationMethod(.catmullRom)
                }
            }
            .chartForegroundStyleScale(range: [.orange, .mint])
            .chartLegend(position: .top, spacing: 6)
            .chartXAxis { AxisMarks { _ in AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5)).foregroundStyle(Color.white.opacity(0.1)); AxisValueLabel().foregroundStyle(.gray) } }
            .chartYAxis { AxisMarks { _ in AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5)).foregroundStyle(Color.white.opacity(0.1)); AxisValueLabel().foregroundStyle(.gray) } }
            .frame(height: 200)
        }
    }

    private func confusionMatrixGrid(_ matrix: [[Int]], classNames: [String], prominent: Bool = false) -> some View {
        let n = matrix.count
        let cellSize: CGFloat = prominent ? 52 : 44
        let cellHeight: CGFloat = prominent ? 36 : 28
        let labelFont = prominent ? Font.subheadline : Font.caption2
        let valueFont = prominent ? Font.body.monospacedDigit() : Font.caption.monospacedDigit()
        return VStack(alignment: .leading, spacing: prominent ? 8 : 4) {
            Grid(horizontalSpacing: prominent ? 10 : 8, verticalSpacing: prominent ? 8 : 6) {
                GridRow {
                    Text("")
                        .frame(width: prominent ? 32 : 24)
                    ForEach(0..<n, id: \.self) { j in
                        Text(classNames.count > j ? classNames[j] : "\(j)")
                            .font(labelFont)
                            .foregroundColor(.gray)
                            .frame(width: cellSize, alignment: .center)
                    }
                }
                ForEach(0..<n, id: \.self) { i in
                    GridRow {
                        Text(classNames.count > i ? classNames[i] : "\(i)")
                            .font(labelFont)
                            .foregroundColor(.gray)
                            .frame(width: prominent ? 32 : 24, alignment: .trailing)
                        ForEach(0..<n, id: \.self) { j in
                            Text("\(matrix[i][j])")
                                .font(valueFont)
                                .foregroundColor(.mint)
                                .frame(width: cellSize, height: cellHeight, alignment: .center)
                                .background(RoundedRectangle(cornerRadius: prominent ? 6 : 4).fill(Color.white.opacity(prominent ? 0.08 : 0.06)))
                        }
                    }
                }
            }
            .padding(prominent ? 12 : 0)
            .background(
                Group {
                    if prominent {
                        RoundedRectangle(cornerRadius: 10).fill(Color.white.opacity(0.04))
                    }
                }
            )
        }
    }

    private var statsCard: some View {
        card(title: "Summary stats (mean ± std, min–max)", icon: "tablecells") {
            if !viewModel.featureStats.isEmpty {
                let displayStats = Array(viewModel.featureStats.prefix(6))
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 200))], spacing: 12) {
                    ForEach(displayStats) { stat in
                        HStack {
                            Text(stat.name)
                                .font(.subheadline)
                                .foregroundColor(.white)
                                .lineLimit(1)
                            Spacer()
                            Text(String(format: "%.2f ± %.2f", stat.mean, stat.std))
                                .font(.caption.monospacedDigit())
                                .foregroundColor(.mint)
                            Text(" [\(String(format: "%.2f", stat.min))–\(String(format: "%.2f", stat.max))]")
                                .font(.caption2)
                                .foregroundColor(.gray)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.white.opacity(0.04))
                        )
                    }
                }
            }
        }
    }

    private func card<Content: View>(title: String, icon: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .foregroundStyle(.mint)
                Text(title)
                    .font(.headline)
                    .foregroundColor(.white)
            }
            content()
        }
        .padding(20)
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(Color.white.opacity(0.06))
        )
    }

    private func axisMarks(_ labelColor: Color) -> some AxisContent {
        AxisMarks { _ in
            AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5))
                .foregroundStyle(Color.white.opacity(0.1))
            AxisValueLabel()
                .foregroundStyle(labelColor)
        }
    }
}

#Preview {
    ContentView()
}
