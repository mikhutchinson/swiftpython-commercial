# SwiftPython Examples

Runnable samples that complement [docs/api-guide](../docs/api-guide/).

| Directory | Purpose |
|-----------|---------|
| [IrisDemo](IrisDemo/) | Full SwiftUI macOS app: bundled Python sklearn service, Charts, JSON boundary |
| [CoreRuntimeSmoke](CoreRuntimeSmoke/) | Minimal CLI: `Python.run` + `sys` / `json` |
| [ProcessPoolSmoke](ProcessPoolSmoke/) | Minimal CLI: `withProcessPool`, `invokeResult`, `math.sqrt` |
| [BridgingRing](BridgingRing/) | Showcase: callbacks + **`WorkerCallbackContext` reentrant** Python↔Swift + **`evalEvents`** generator/progress streams over the pool IPC |
| [SharedTensorPipeline](SharedTensorPipeline/) | **Headline demo.** Pool-arena POSIX shared memory (`createSharedTensor` + `withSharedBuffer`) and out-of-band streaming (`SharedRingBuffer` + `startOutOfBandStream`) wired together with a liveness proof on the same worker. |

Dependencies resolve the parent `swiftpython-commercial` checkout by path (same
as IrisDemo). For a remote SPM dependency, use the env vars documented in each
example’s `Package.swift`:
`SWIFTPYTHON_COMMERCIAL_PACKAGE_URL` and `SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION`.

## Worker path for pool examples (`ProcessPoolSmoke`, `BridgingRing`)

`SwiftPythonWorker` must come from **the same `swiftpython-commercial` release /
checkout as the XCFramework**, not ad‑hoc guesses or unrelated build trees:

```bash
# From the distro root directory (recommended)
SWIFTPYTHON_WORKER_PATH="$PWD/SwiftPythonWorker" swift run --package-path Examples/ProcessPoolSmoke
SWIFTPYTHON_WORKER_PATH="$PWD/SwiftPythonWorker" swift run --package-path Examples/BridgingRing
SWIFTPYTHON_WORKER_PATH="$PWD/SwiftPythonWorker" swift run -c release --package-path Examples/SharedTensorPipeline
```

Or change into the package directory first and resolve two levels upward:

```bash
cd Examples/BridgingRing
SWIFTPYTHON_WORKER_PATH="$PWD/../../SwiftPythonWorker" swift run
```
