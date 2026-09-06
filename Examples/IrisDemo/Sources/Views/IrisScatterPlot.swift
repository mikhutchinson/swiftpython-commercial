import SwiftUI
import Charts

struct IrisScatterPlot: View {
    @Bindable var model: IrisViewModel
    private let palette: [Color] = [.teal, .orange, .indigo]

    var body: some View {
        let xFeature = model.xFeature
        let yFeature = model.yFeature
        if let dataset = model.dataset,
           dataset.featureNames.indices.contains(xFeature),
           dataset.featureNames.indices.contains(yFeature) {
            plot(dataset: dataset, xFeature: xFeature, yFeature: yFeature,
                 samples: model.visibleSamples, predictions: model.predictionsByID,
                 selectedSampleID: model.selectedSampleID, hasResult: model.result != nil)
        }
    }

    // Charts retains and reevaluates mark builders during view updates. Capture
    // every plotting input together; an old point must never read a new dataset's
    // axes or class names (or "Unknown" while the dataset is being replaced).
    private func plot(dataset: IrisDatasetPayload, xFeature: Int, yFeature: Int,
                      samples: [DataPoint], predictions: [Int: SamplePrediction],
                      selectedSampleID: Int?, hasResult: Bool) -> some View {
        let xName = dataset.featureNames[xFeature]
        let yName = dataset.featureNames[yFeature]
        return VStack(spacing: 10) {
            HStack {
                axisPicker("X axis", names: dataset.featureNames, selection: $model.xFeature)
                axisPicker("Y axis", names: dataset.featureNames, selection: $model.yFeature)
            }
            Chart(samples) { point in
                PointMark(x: .value(xName, point.values[xFeature]), y: .value(yName, point.values[yFeature]))
                    .foregroundStyle(by: .value("Class", dataset.classNames[point.classID]))
                    .symbolSize(point.id == selectedSampleID ? 120 : 42)
                    .opacity(selectedSampleID == nil || point.id == selectedSampleID ? 1 : 0.55)
                    .accessibilityLabel("Sample \(point.id + 1), \(dataset.classNames[point.classID])")
                    .accessibilityValue("\(xName) \(point.values[xFeature]), \(yName) \(point.values[yFeature])")
                if predictions[point.id]?.isMistake == true {
                    PointMark(x: .value(xName, point.values[xFeature]), y: .value(yName, point.values[yFeature]))
                        .foregroundStyle(Color.primary)
                        .symbol { Image(systemName: "xmark").font(.system(size: 10, weight: .heavy)).foregroundStyle(.primary) }
                }
                if point.id == selectedSampleID {
                    PointMark(x: .value(xName, point.values[xFeature]), y: .value(yName, point.values[yFeature]))
                        .foregroundStyle(Color.primary)
                        .symbol { Circle().stroke(.primary, lineWidth: 2).frame(width: 17, height: 17) }
                }
            }
            .chartForegroundStyleScale(domain: dataset.classNames, range: palette)
            .chartXAxisLabel(xName).chartYAxisLabel(yName)
            .chartLegend(position: .bottom, alignment: .leading)
            .chartOverlay { proxy in
                GeometryReader { geometry in
                    Rectangle().fill(.clear).contentShape(Rectangle())
                        .onTapGesture { location in
                            guard let anchor = proxy.plotFrame else { return }
                            let frame = geometry[anchor]
                            guard frame.contains(location) else { return }
                            let position = CGPoint(x: location.x - frame.minX, y: location.y - frame.minY)
                            var closest: (id: Int, distance: CGFloat)?
                            for point in samples {
                                guard let x = proxy.position(forX: point.values[xFeature]),
                                      let y = proxy.position(forY: point.values[yFeature]) else { continue }
                                let distance = hypot(x - position.x, y - position.y)
                                if distance < (closest?.distance ?? 22) { closest = (point.id, distance) }
                            }
                            if let closest { model.selectedSampleID = closest.id }
                        }
                }
            }
            HStack {
                Text("Click a point or select a row below.")
                Spacer()
                if hasResult { Label("Held-out mistake", systemImage: "xmark") }
            }.font(.caption).foregroundStyle(.secondary)
        }
    }

    private func axisPicker(_ title: String, names: [String], selection: Binding<Int>) -> some View {
        Picker(title, selection: selection) {
            ForEach(Array(names.enumerated()), id: \.offset) { index, name in
                Text(name).tag(index)
            }
        }.pickerStyle(.menu)
    }
}
