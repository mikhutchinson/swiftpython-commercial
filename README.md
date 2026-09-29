# SwiftPython

**Python libraries, native Swift apps.**

Call Python from Swift, keep models loaded in worker processes, stream results,
and connect numerical workloads to native audio and Metal.
SwiftPython includes Python 3.13, so your users do not need to install Python.

[Get started](docs/api-guide/) · [Examples](Examples/) ·
[Website](https://swiftpython.dev) ·
[Download 0.7.0-preview.1](https://github.com/mikhutchinson/swiftpython-commercial/releases/tag/v0.7.0-preview.1)

## Make your first call

Add the package in Xcode with **Exact Version** `0.7.0-preview.1`, or use SwiftPM:

```swift
.package(
    url: "https://github.com/mikhutchinson/swiftpython-commercial.git",
    exact: "0.7.0-preview.1"
)
```

Add `SwiftPythonRuntime` to your app target:

```swift
.product(name: "SwiftPythonRuntime", package: "swiftpython-commercial")
```

Then call Python from an asynchronous function or task:

```swift
import SwiftPythonRuntime

let result: Double = try await Python.run {
    let math = try Python.import("math")
    return try Double(pythonObject: try math.sqrt(144.0))
}
print(result) // 12.0
```

Use `Python.run` for short calls in your app process. For heavier work, a
`PythonProcessPool` runs independent Python interpreters in worker processes:

```swift
try await withProcessPool(workers: 2) { pool in
    let result: Double = try await pool.invokeResult(
        module: "math",
        function: "sqrt",
        args: [.python(144.0)]
    )
    print(result)
}
```

The scoped helper waits for worker shutdown when the work finishes or throws.
For this sidecar example, embed the matching `SwiftPythonWorker` from the complete
distribution in your app's `Contents/MacOS` directory.

## Build something

- **[Particle Showcase](Examples/ParticleShowcase/):** NumPy moves 1,048,576
  particles while Swift and Metal render their shared positions. Try the live
  controls or export a video.
- **[Iris](Examples/IrisDemo/):** explore datasets, train scikit-learn classifiers,
  inspect held-out mistakes, and change measurements to get new predictions.

```sh
git clone https://github.com/mikhutchinson/swiftpython-commercial.git
cd swiftpython-commercial
Examples/IrisDemo/scripts/build_app.sh --open
```

The example builders download their dependencies and create development apps
that run offline. [See both examples and their prerequisites.](Examples/)

## What's new in Preview 1

Preview 1 adds app-owned XPC and ExtensionFoundation workers through
`SwiftPythonWorkerService` and the [consumer assembly kit](Consumer/).
It includes explicit worker context configuration, recoverable logical sessions,
and improvements to callback ownership, worker shutdown and VM restore behavior.

This release supplies macOS binaries for Apple Silicon and Intel. macOS 15 or
later is required; ExtensionFoundation hosting requires macOS 26. The iOS demo
on the website shows four native Python workers computing a Julia fractal in
an iPad app.

## Choose your products

| Product | Use it for |
|---|---|
| `SwiftPythonRuntime` | Python calls, process pools, retained objects, callbacks, streaming, task graphs and duplex sessions |
| `SwiftPythonWorkerService` | Workers hosted in your app's XPC services or extensions |
| `SwiftPythonAudioInterop` | Native audio capture and playback with duplex sessions |
| `SwiftPythonMetalInterop` | Metal buffer integration and GPU ownership |

The package includes the Python runtime and Engine dependencies automatically.
Import the public products your app uses. Bundle additional Python packages,
such as NumPy, for the supplied Python 3.13 runtime and your target architecture.

## Package your app

Download the **complete distribution** for the matched worker, audio probe,
consumer kit, VM helpers, examples and entitlement templates. SwiftPM downloads
the six XCFramework assets used by the four products.

For a sidecar app, the basic layout is:

```text
YourApp.app/
  Contents/
    Frameworks/
      SwiftPythonEngine.framework/
      Python.framework/
    MacOS/
      YourApp
      SwiftPythonWorker
```

Embed Engine once and sign nested code before signing the outer app. Xcode links
and embeds Python through the package dependency; no host interpreter,
`PYTHONHOME` or custom Python linker paths are needed.

For XPC services or extensions, follow the [consumer kit](Consumer/README.md).
For audio readiness checks, embed the matching `SwiftPythonAudioProbe` at
`Contents/MacOS/SwiftPythonAudioProbe`, configure microphone permission on the
parent app, and follow [the audio guide](docs/api-guide/ch11-apple-interop.md).

Keep binaries and helpers on the same release. Use the supplied
[entitlement templates](Entitlements/) for your app's sandbox policy, then sign
and notarize your finished application for distribution.

[Packaging guide](https://swiftpython.dev/docs/packaging/) ·
[API guide](docs/api-guide/) ·
[All releases](https://github.com/mikhutchinson/swiftpython-commercial/releases)

## License

See [LICENSE](LICENSE) for the terms and [LICENSING.md](LICENSING.md) for common questions.
