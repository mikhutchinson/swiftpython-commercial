import AppKit
import MetalKit
import SwiftUI

@MainActor @Observable
final class ShowcaseModel {
    var renderer: ParticleRenderer?
    var receipt: FrameReceipt?
    var formation = Formation.galaxy
    var paused = false
    var automatic = true
    var fps = 0.0
    var workerPID: Int32 = 0
    var error: String?
    var exporting = false
    var exportProgress = 0.0
    var savedClip: URL?
    var orbit: Float = 0
    private var burst = 0
    private var liveTask: Task<Void, Never>?
    private var exportTask: Task<Void, Never>?
    private var reduceMotion = false

    func start(reduceMotion: Bool) {
        guard liveTask == nil else { return }
        self.reduceMotion = reduceMotion
        paused = reduceMotion
        automatic = !reduceMotion
        receipt = nil
        fps = 0
        liveTask = Task { await live() }
    }

    private func live() async {
        var ownedEngine: ParticleEngine?
        do {
            let engine = try await ParticleEngine()
            ownedEngine = engine
            try Task.checkCancellation()
            renderer = engine.renderer
            workerPID = engine.workerPID
            var simulationSeconds = 0.0
            var previous = ContinuousClock.now
            var fpsEpoch = previous
            var framesInEpoch = 0
            while !Task.isCancelled {
                if paused && receipt != nil {
                    previous = .now
                    fpsEpoch = .now
                    framesInEpoch = 0
                    try await Task.sleep(for: .milliseconds(40))
                    continue
                }
                let began = ContinuousClock.now
                let delta = previous.duration(to: began)
                let wallDT = Double(delta.components.seconds) + Double(delta.components.attoseconds) / 1e18
                previous = began
                let dt = max(1 / 120.0, min(0.05, wallDT))
                if automatic { formation = .at(seconds: simulationSeconds.truncatingRemainder(dividingBy: 22)) }
                let next = try await engine.frame(mode: formation, seconds: simulationSeconds,
                                                  dt: dt, burst: burst, orbit: orbit)
                receipt = next
                simulationSeconds += dt
                framesInEpoch += 1
                let period = fpsEpoch.duration(to: .now)
                let elapsed = Double(period.components.seconds) + Double(period.components.attoseconds) / 1e18
                if elapsed >= 0.5 {
                    fps = Double(framesInEpoch) / elapsed
                    framesInEpoch = 0
                    fpsEpoch = .now
                }
                // Cap the producer at 60; a slow frame is never queued behind
                // another Python/GPU frame. The UI's number is measured wall FPS.
                let deadline = began.advanced(by: .nanoseconds(16_666_667))
                if ContinuousClock.now < deadline { try await Task.sleep(until: deadline, clock: .continuous) }
            }
        } catch is CancellationError {
        } catch {
            self.error = String(describing: error)
        }
        if let ownedEngine { await ownedEngine.shutdown() }
    }

    func select(_ selected: Formation) {
        automatic = false
        formation = selected
        paused = false
    }

    func scatter() {
        burst += 1
        paused = false
    }

    func chooseExport() {
        guard !exporting else { return }
        let panel = NSOpenPanel()
        panel.title = "Choose a folder for the clip"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Save clip here"
        guard panel.runModal() == .OK, let folder = panel.url else { return }
        let destination = folder.appendingPathComponent("SwiftPython-\(Int(Date().timeIntervalSince1970))")
        exportTask = Task {
            exporting = true
            exportProgress = 0
            liveTask?.cancel()
            await liveTask?.value
            liveTask = nil
            var ownedEngine: ParticleEngine?
            do {
                let engine = try await ParticleEngine()
                ownedEngine = engine
                renderer = engine.renderer
                workerPID = engine.workerPID
                savedClip = try await ShowcaseExport.run(engine: engine, options: ExportOptions(directory: destination)) { progress, frame in
                    self.exportProgress = progress
                    self.receipt = frame
                    self.formation = Formation(rawValue: frame.formation) ?? .galaxy
                }
            } catch is CancellationError {
            } catch {
                self.error = String(describing: error)
            }
            if let ownedEngine { await ownedEngine.shutdown() }
            exporting = false
            if !Task.isCancelled { start(reduceMotion: reduceMotion) }
        }
    }

    func stop() async {
        exportTask?.cancel()
        liveTask?.cancel()
        await exportTask?.value
        await liveTask?.value
        exportTask = nil
        liveTask = nil
    }
}

struct ParticleCanvas: NSViewRepresentable {
    let renderer: ParticleRenderer
    let frame: Int

    func makeNSView(context: Context) -> MTKView {
        let view = MTKView(frame: .zero, device: renderer.device)
        view.colorPixelFormat = .bgra8Unorm
        view.isPaused = true
        view.enableSetNeedsDisplay = false
        view.framebufferOnly = true
        view.clearColor = MTLClearColorMake(0.006, 0.009, 0.018, 1)
        view.delegate = context.coordinator
        return view
    }
    func updateNSView(_ view: MTKView, context: Context) {
        context.coordinator.renderer = renderer
        view.draw()
    }
    func makeCoordinator() -> Coordinator { Coordinator(renderer) }

    @MainActor final class Coordinator: NSObject, MTKViewDelegate {
        var renderer: ParticleRenderer
        init(_ renderer: ParticleRenderer) { self.renderer = renderer }
        func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) { view.draw() }
        func draw(in view: MTKView) { renderer.present(in: view) }
    }
}

struct ShowcaseView: View {
    @Bindable var model: ShowcaseModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                Color(red: 0.006, green: 0.009, blue: 0.018)
                if let renderer = model.renderer {
                    ParticleCanvas(renderer: renderer, frame: model.receipt?.frame ?? 0)
                        .aspectRatio(16 / 9.0, contentMode: .fit)
                        .accessibilityLabel("One million particles computed in a Python worker, rendered by Metal. Formation: \(model.formation.title).")
                        .accessibilityAddTraits(.isImage)
                }
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("1,048,576").font(.system(size: 48, weight: .semibold, design: .rounded)).monospacedDigit()
                            Text("PARTICLES / COMPUTED IN PYTHON").font(.system(size: 12, weight: .medium)).tracking(1.5).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 7) {
                            Text("SwiftPython").font(.system(size: 25, weight: .semibold))
                            Text("NumPy → shared tensor → Metal").font(.system(size: 12)).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Text(model.formation.caption).font(.system(size: 22, weight: .medium))
                    Divider().padding(.vertical, 9)
                    HStack(spacing: 28) {
                        metric(model.exporting ? "EXPORT" : "LIVE",
                               model.exporting ? "\(Int(model.exportProgress * 100))%"
                                 : String(format: "%.0f fps", model.paused ? 0 : model.fps), accent: true)
                        metric("PYTHON", String(format: "%.1f ms", model.receipt?.pythonMS ?? 0))
                        metric("METAL", String(format: "%.1f ms", model.receipt?.gpu.milliseconds ?? 0))
                        metric("PARTICLE BUFFER", "16 MiB")
                        Spacer(minLength: 0)
                        Text(model.receipt.map { $0.gpu.particleBytesCopied == 0
                            ? "0 particle payload copies" : "Particle upload copy" } ?? "Waiting for buffer route")
                            .font(.system(size: 12)).foregroundStyle(.secondary)
                    }
                }
                .padding(36)
                .allowsHitTesting(false)
                if model.renderer == nil {
                    ProgressView("Starting NumPy worker…").controlSize(.large)
                }
                if model.exporting {
                    VStack(spacing: 14) {
                        Text("Rendering your clip").font(.title2)
                        ProgressView(value: model.exportProgress).frame(width: 300)
                        Text("1080p · 22 seconds · actual simulation frames").foregroundStyle(.secondary)
                    }
                    .padding(30).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
                }
            }
            .frame(minHeight: 560)
            HStack(spacing: 16) {
                Picker("Formation", selection: Binding(get: { model.formation }, set: { model.select($0) })) {
                    ForEach(Formation.allCases) { Text($0.title).tag($0) }
                }.pickerStyle(.segmented).frame(maxWidth: 470)
                Button(model.paused ? "Resume" : "Pause", systemImage: model.paused ? "play.fill" : "pause.fill") {
                    model.paused.toggle()
                }.keyboardShortcut(.space, modifiers: [])
                Button("Scatter", systemImage: "sparkles") { model.scatter() }.keyboardShortcut("b", modifiers: [])
                Toggle("Auto", isOn: $model.automatic).toggleStyle(.button).help("Cycle through all four formations")
                Spacer()
                if let clip = model.savedClip {
                    Button("Open clip") { NSWorkspace.shared.open(clip) }
                }
                Button("Save 22s clip", systemImage: "square.and.arrow.down") { model.chooseExport() }
                    .keyboardShortcut("s", modifiers: .command)
            }
            .disabled(model.renderer == nil || model.exporting)
            .padding(.horizontal, 24).padding(.vertical, 18)
            .background(.bar)
            if let error = model.error {
                Text(error).foregroundStyle(.red).textSelection(.enabled).padding()
            }
        }
        .frame(minWidth: 1100, minHeight: 680)
        .preferredColorScheme(.dark)
        .task { model.start(reduceMotion: reduceMotion) }
        .onChange(of: reduceMotion) { _, enabled in
            if enabled { model.paused = true; model.automatic = false }
        }
    }

    private func metric(_ label: String, _ value: String, accent: Bool = false) -> some View {
        HStack(spacing: 7) {
            Text(label).foregroundStyle(.secondary)
            Text(value).foregroundStyle(accent ? Color.orange : .white).monospacedDigit()
        }.font(.system(size: 12, weight: .medium))
    }
}

@MainActor
final class ShowcaseAppDelegate: NSObject, NSApplicationDelegate {
    var model: ShowcaseModel?
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task {
            await model?.stop()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

struct ParticleShowcaseApp: App {
    @NSApplicationDelegateAdaptor(ShowcaseAppDelegate.self) private var delegate
    @State private var model = ShowcaseModel()
    var body: some Scene {
        Window("SwiftPython · Particle Showcase", id: "showcase") {
            ShowcaseView(model: model).onAppear { delegate.model = model }
        }
        .defaultSize(width: 1344, height: 824)
        .windowStyle(.hiddenTitleBar)
    }
}
