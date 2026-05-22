# Process Pool Smoke

Tiny macOS CLI that exercises **`withProcessPool`** and `invokeResult` against
workers running CPython—the same shape as the second README smoke test (`math.sqrt`).

## Run

Use the **`SwiftPythonWorker`** that ships next to **`SwiftPythonRuntime.xcframework`**
in this **same `swiftpython-commercial` distro** — not binaries from unrelated
directories.

Either run from the **repository root**:

```bash
SWIFTPYTHON_WORKER_PATH="$PWD/SwiftPythonWorker" swift run --package-path Examples/ProcessPoolSmoke
```

or `cd Examples/ProcessPoolSmoke` then:

```bash
SWIFTPYTHON_WORKER_PATH="$PWD/../../SwiftPythonWorker" swift run
```

Without that, the runtime still looks for `SwiftPythonWorker` next to your built
binary or on `PATH`; see the [root README](../../README.md#app-bundle-layout).

## What it proves

- The worker sidecar launches and accepts pool commands.
- Module-level invokes return converted Swift values (`Double`).
