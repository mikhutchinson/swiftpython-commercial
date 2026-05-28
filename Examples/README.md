# SwiftPython Examples

Runnable samples that complement [docs/api-guide](../docs/api-guide/).

| Directory | Purpose |
|-----------|---------|
| [IrisDemo](IrisDemo/) | Full SwiftUI macOS app: bundled Python sklearn service, Charts, JSON boundary |
| [CoreRuntimeSmoke](CoreRuntimeSmoke/) | Minimal CLI: `Python.run` + `sys` / `json` |
| [ProcessPoolSmoke](ProcessPoolSmoke/) | Minimal CLI: `withProcessPool`, `invokeResult`, `math.sqrt` |
| [BridgingRing](BridgingRing/) | Showcase: callbacks + **`WorkerCallbackContext` reentrant** Python↔Swift + **`evalEvents`** generator/progress streams over the pool IPC |
| [SharedTensorPipeline](SharedTensorPipeline/) | **Headline demo.** Pool-arena POSIX shared memory (`createSharedTensor` + `withSharedBuffer`) and out-of-band streaming (`SharedRingBuffer` + `startOutOfBandStream`) wired together with a liveness proof on the same worker. |

Dependencies resolve the parent checkout by path, so these examples work from a
normal local clone or release folder even if the directory is renamed. For a
remote SPM dependency, set `SWIFTPYTHON_COMMERCIAL_PACKAGE_URL` and
`SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION`; the local path mode is the default.

## Run

From the distribution root:

```bash
swift run swiftpython-smoke
swift run --package-path Examples/CoreRuntimeSmoke
swift run --package-path Examples/ProcessPoolSmoke
swift run --package-path Examples/BridgingRing
swift run -c release --package-path Examples/SharedTensorPipeline
scripts/consumer_path_smoke.sh
```

Or change into an example package directory and run it directly:

```bash
cd Examples/BridgingRing
swift run
```

The pool examples use the `SwiftPythonWorker` from this same checkout. If you
embed the runtime in your own app, keep the worker and XCFramework on the same
release tag and copy/re-sign the worker into your app bundle.

The consumer smoke script creates a temporary external Swift package and depends
on this checkout by local path. It catches package-name and worker-discovery
regressions that do not show up when running examples from inside the repo.

The example manifests auto-detect Homebrew's Apple Silicon and Intel Python
3.13 prefixes. For custom Python layouts, set `PYTHON_HOME`, `PYTHONHOME`, or
`SWIFTPYTHON_PYTHON_LIB_DIR`.
