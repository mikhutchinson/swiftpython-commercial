# BridgingRing

Showcase for **SwiftPython process-pool features** that classic in-process bridges
(PythonKit-style) cannot safely reproduce:

1. **Isolated interpreter** (`PythonProcessPool`), so hostile or CPU-heavy Python
   stays outside your app binary and its GIL stalls your UI less.
2. **`swift_bridge` callbacks**: worker Python invokes Swift by stable name (`merge`,
   `hardware_tag`).
3. **Reentrant callbacks**: Swift’s handler calls back into **the same** worker via
   `WorkerCallbackContext.evalResult`, reading live globals (`accumulator`) without a
   self-deadlock (see [Chapter 7 — Callbacks](../../docs/api-guide/ch7-callbacks.md)).
4. **`evalEvents`**: a Python generator emits **regular values** and **`swift_bridge.progress`**
   hints on **one ordered** Swift `AsyncSequence` (`StreamEvent`), with cooperative
   cancellation via `swift_bridge.check_cancel()` (see [Chapter 5](../../docs/api-guide/ch5-streaming.md)).

## Run

Use **`SwiftPythonWorker`** from **this checkout or release tarball** (same pairing
as the XCFramework dependency), never a stray path you assume on disk:

```bash
# From distro root — replace with your clone path deliberately as needed:
SWIFTPYTHON_WORKER_PATH="$PWD/SwiftPythonWorker" swift run --package-path Examples/BridgingRing
```

Or from **`Examples/BridgingRing`**:

```bash
SWIFTPYTHON_WORKER_PATH="$PWD/../../SwiftPythonWorker" swift run
```

## Expected output

- `fusion_report(0.125)` → `9000.125` — Python rang Swift during `merge`, Swift
  consulted `accumulator` on that interpreter, then layered the addend.
- `host_probe()` — Swift-only `sysctlbyname("hw.model")` + OS string, returned across IPC.
- The `evalEvents` section interleaves `scale-N` progress lines with yielded values.
