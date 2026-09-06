import SwiftUI
import Charts

struct IrisScatterPlot: View {
    @Bindable var model: IrisViewModel
    private let palette: [Color] = [.teal, .orange, .indigo]

    var body: some View {
        VStack(spacing: 10) {
            HStack {
                axisPicker("X axis", selection: $model.xFeature)
                axisPicker("Y axis", selection: $model.yFeature)
            }
            let predictions = model.predictionsByID
            Chart(model.visibleSamples) { point in
                PointMark(x: .value(xName, point.values[model.xFeature]), y: .value(yName, point.values[model.yFeature]))
                    .foregroundStyle(by: .value("Class", model.className(point.classID)))
                    .symbolSize(point.id == model.selectedSampleID ? 120 : 42)
                    .opacity(model.selectedSampleID == nil || point.id == model.selectedSampleID ? 1 : 0.55)
                    .accessibilityLabel("Sample \(point.id + 1), \(model.className(point.classID))")
                    .accessibilityValue("\(xName) \(point.values[model.xFeature]), \(yName) \(point.values[model.yFeature])")
                if predictions[point.id]?.isMistake == true {
                    PointMark(x: .value(xName, point.values[model.xFeature]), y: .value(yName, point.values[model.yFeature]))
                        .symbol { Image(systemName: "xmark").font(.system(size: 10, weight: .heavy)).foregroundStyle(.primary) }
                }
                if point.id == model.selectedSampleID {
                    PointMark(x: .value(xName, point.values[model.xFeature]), y: .value(yName, point.values[model.yFeature]))
                        .symbol { Circle().stroke(.primary, lineWidth: 2).frame(width: 17, height: 17) }
                }
            }
            .chartForegroundStyleScale(domain: model.dataset?.classNames ?? [], range: palette)
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
                            for point in model.visibleSamples {
                                guard let x = proxy.position(forX: point.values[model.xFeature]),
                                      let y = proxy.position(forY: point.values[model.yFeature]) else { continue }
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
                if model.result != nil { Label("Held-out mistake", systemImage: "xmark") }
            }.font(.caption).foregroundStyle(.secondary)
        }
    }

    private var xName: String { model.dataset?.featureNames[model.xFeature] ?? "X" }
    private var yName: String { model.dataset?.featureNames[model.yFeature] ?? "Y" }
    private func axisPicker(_ title: String, selection: Binding<Int>) -> some View {
        Picker(title, selection: selection) {
            ForEach(Array((model.dataset?.featureNames ?? []).enumerated()), id: \.offset) { index, name in
                Text(name).tag(index)
            }
        }.pickerStyle(.menu)
    }
}
