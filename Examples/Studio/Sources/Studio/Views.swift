import SwiftUI
import Charts

struct ContentView: View {
    @StateObject private var vm = BakeOffViewModel()

    var body: some View {
        VStack(spacing: 14) {
            header
            HStack(alignment: .top, spacing: 14) {
                codePanel.frame(width: 420)
                resultsPanel
            }
            workerStrip
            ConsoleView(lines: vm.log)
                .frame(maxWidth: .infinity, minHeight: 190, maxHeight: 210)
            footer
        }
        .padding(22)
        .frame(width: 1000, height: 1000)
        .background(Theme.bg)
        .preferredColorScheme(.dark)
        .task {
            // Optional auto-run for screen capture / demos.
            if ProcessInfo.processInfo.environment["STUDIO_AUTORUN"] == "1" {
                try? await Task.sleep(nanoseconds: 1_200_000_000)
                vm.run()
            }
        }
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 2) {
                Text("SwiftPython Studio").font(Theme.title).foregroundStyle(Theme.text)
                Text("Swift driving scikit-learn — across real Python processes, live.")
                    .font(.system(size: 14)).foregroundStyle(Theme.dim)
            }
            Spacer()
            if vm.elapsedMs > 0 {
                badge("\(vm.elapsedMs) ms", color: Theme.python)
            }
            Button(action: { vm.run() }) {
                HStack(spacing: 8) {
                    Image(systemName: vm.phase == .running ? "hourglass" : "play.fill")
                    Text(vm.phase == .running ? "Racing…" : "Run bake-off")
                }
                .font(.system(size: 16, weight: .bold, design: .rounded))
                .padding(.horizontal, 18).padding(.vertical, 11)
                .background(Theme.accent).foregroundStyle(.black)
                .clipShape(Capsule())
            }
            .buttonStyle(.plain)
            .disabled(vm.phase == .running)
            .opacity(vm.phase == .running ? 0.6 : 1)
        }
    }

    // MARK: Live code

    private var codePanel: some View {
        panel("Live Swift") {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(vm.candidates) { c in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 8) {
                            statusDot(c.status, color: Theme.modelColors[c.colorIndex])
                            Text(c.name).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.text)
                            Spacer()
                            Text("worker \(c.workerIndex)").font(Theme.monoSmall).foregroundStyle(Theme.dim)
                        }
                        Text(c.code)
                            .font(Theme.monoSmall)
                            .foregroundStyle(Theme.text.opacity(0.92))
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Theme.term)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }
        }
    }

    // MARK: Results

    private var resultsPanel: some View {
        panel("Test accuracy") {
            VStack(alignment: .leading, spacing: 14) {
                Chart(vm.candidates) { c in
                    BarMark(
                        x: .value("Accuracy", c.accuracy ?? 0),
                        y: .value("Model", c.name)
                    )
                    .foregroundStyle(Theme.modelColors[c.colorIndex])
                    .annotation(position: .trailing) {
                        if let a = c.accuracy {
                            Text(String(format: "%.3f", a)).font(Theme.monoSmall).foregroundStyle(Theme.text)
                        }
                    }
                    .cornerRadius(4)
                }
                .chartXScale(domain: 0...1)
                .chartXAxis { AxisMarks(values: [0, 0.25, 0.5, 0.75, 1.0]) }
                .frame(height: 180)
                .animation(.easeOut(duration: 0.5), value: vm.candidates.map(\.accuracy))

                if let cm = vm.confusion {
                    ConfusionView(matrix: cm, winner: vm.winnerName)
                } else {
                    Text("Run the bake-off to race four models in parallel.")
                        .font(.system(size: 13)).foregroundStyle(Theme.dim)
                        .frame(maxWidth: .infinity, minHeight: 150, alignment: .center)
                }
            }
        }
    }

    // MARK: Worker strip

    private var workerStrip: some View {
        panel("Python workers") {
            HStack(spacing: 10) {
                ForEach(vm.workers) { w in
                    HStack(spacing: 8) {
                        statusDot(w.status, color: w.colorIndex.map { Theme.modelColors[$0] } ?? Theme.idle)
                        VStack(alignment: .leading, spacing: 1) {
                            Text("worker \(w.id)").font(.system(size: 12, weight: .bold)).foregroundStyle(Theme.text)
                            Text(w.status == .idle ? "idle" : w.label)
                                .font(Theme.monoSmall).foregroundStyle(Theme.dim).lineLimit(1)
                        }
                        Spacer()
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity)
                    .background(Theme.term)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(
                        w.status == .training ? Theme.busy.opacity(0.7) : Theme.panelStroke, lineWidth: 1))
                }
            }
        }
    }

    private var footer: some View {
        HStack {
            Text("SwiftPythonRuntime 0.5.14").font(Theme.monoSmall).foregroundStyle(Theme.dim)
            Spacer()
            Text("pool.<module> · zero-copy handles · multi-process")
                .font(Theme.monoSmall).foregroundStyle(Theme.dim)
        }
    }

    // MARK: Building blocks

    private func panel<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased())
                .font(.system(size: 11, weight: .bold)).tracking(1.2).foregroundStyle(Theme.dim)
            content()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.panel)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.panelStroke, lineWidth: 1))
    }

    private func badge(_ text: String, color: Color) -> some View {
        Text(text).font(.system(size: 13, weight: .bold, design: .monospaced))
            .padding(.horizontal, 12).padding(.vertical, 7)
            .background(color.opacity(0.18)).foregroundStyle(color)
            .clipShape(Capsule())
    }

    private func statusDot(_ status: RunStatus, color: Color) -> some View {
        Circle()
            .fill(status == .idle ? Theme.idle : (status == .done ? Theme.good : color))
            .frame(width: 10, height: 10)
            .overlay(Circle().stroke(.white.opacity(0.15), lineWidth: 1))
            .opacity(status == .training ? 0.5 : 1)
            .animation(status == .training ? .easeInOut(duration: 0.7).repeatForever(autoreverses: true) : .default,
                       value: status)
    }
}

// MARK: - Confusion matrix

struct ConfusionView: View {
    let matrix: ConfusionMatrix
    let winner: String?

    private var maxCount: Int { matrix.counts.flatMap { $0 }.max() ?? 1 }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Confusion matrix").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.text)
                if let winner { Text("· \(winner)").font(Theme.monoSmall).foregroundStyle(Theme.good) }
            }
            Grid(horizontalSpacing: 4, verticalSpacing: 4) {
                ForEach(matrix.counts.indices, id: \.self) { r in
                    GridRow {
                        ForEach(matrix.counts[r].indices, id: \.self) { c in
                            let v = matrix.counts[r][c]
                            Text("\(v)")
                                .font(Theme.monoSmall)
                                .frame(width: 54, height: 38)
                                .background(Theme.good.opacity(Double(v) / Double(maxCount) * 0.75 + (r == c ? 0.12 : 0)))
                                .foregroundStyle(Theme.text)
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                        }
                    }
                }
            }
            Text("rows = true class · columns = predicted")
                .font(.system(size: 11)).foregroundStyle(Theme.dim)
        }
    }
}

// MARK: - Terminal-style console ("behind the curtain")

struct ConsoleView: View {
    let lines: [LogLine]

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(lines) { line in
                        HStack(alignment: .top, spacing: 6) {
                            Text(prefix(line.kind)).foregroundStyle(color(line.kind)).font(Theme.monoSmall)
                            Text(line.text).foregroundStyle(color(line.kind)).font(Theme.monoSmall)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .id(line.id)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Theme.term)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.panelStroke, lineWidth: 1))
            .onChange(of: lines.count) { _, _ in
                withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
            }
        }
    }

    private func prefix(_ k: LogLine.Kind) -> String {
        switch k {
        case .prompt: return "›"
        case .result: return "✓"
        case .info: return "•"
        case .dim: return " "
        }
    }
    private func color(_ k: LogLine.Kind) -> Color {
        switch k {
        case .prompt: return Theme.accent
        case .result: return Theme.good
        case .info: return Theme.text.opacity(0.85)
        case .dim: return Theme.dim
        }
    }
}
