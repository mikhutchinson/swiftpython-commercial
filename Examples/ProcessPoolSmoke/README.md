# Process Pool Smoke

Tiny macOS CLI that exercises **`withProcessPool`** and `invokeResult` against
workers running CPython—the same shape as the second README smoke test (`math.sqrt`).

## Run

From the repository root:

```bash
swift run --package-path Examples/ProcessPoolSmoke
```

Or from this package directory:

```bash
swift run
```

The runtime discovers the `SwiftPythonWorker` from this same checkout. For your
own app, ship the worker and XCFramework from the same release tag; see the
[root README](../../README.md#app-bundle-layout).

## What it proves

- The worker sidecar launches and accepts pool commands.
- Module-level invokes return converted Swift values (`Double`).
