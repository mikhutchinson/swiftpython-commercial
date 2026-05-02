# SwiftPython API Guide

This guide is split into chapters. Read the chapter(s) relevant to your task.

| Chapter | File | When to read |
|---------|------|-------------|
| 1 — Core Runtime | [ch1-core-runtime.md](ch1-core-runtime.md) | `Python.run`, `PyObjectRef`, `PythonError`, context managers |
| 2 — Type Conversion & Buffers | [ch2-data-interop.md](ch2-data-interop.md) | Swift↔Python value conversion, slicing, NumPy buffers, Accelerate, callables |
| 3 — Concurrency & Handles | [ch3-concurrency-handles.md](ch3-concurrency-handles.md) | `PythonExecutor`, `PyHandle`, `withGIL` |
| 4 — ProcessPool | [ch4-process-pool.md](ch4-process-pool.md) | Multi-worker execution, `eval`, `invoke`, `method`, backpressure, lifecycle |
| 5 — Streaming | [ch5-streaming.md](ch5-streaming.md) | `CancellableStream`, generator iteration over IPC |
| 6 — DAG Orchestration | [ch6-dag.md](ch6-dag.md) | `ProcessPoolDAG`, dependency graphs, failure policies |
| 7 — Bidirectional Callbacks | [ch7-callbacks.md](ch7-callbacks.md) | Python→Swift sync/async/reentrant/streaming callbacks |
| 8 — Generated Modules | [ch8-generated-modules.md](ch8-generated-modules.md) | NumPy, Pandas, Sklearn, MLX, Transformers, etc. |


## Quick API Decision Map

| I want to… | Use |
|------------|-----|
| Run Python in-process, get a Swift value back | `Python.run { }` → `PythonConvertible` (ch1, ch2) |
| Keep a Python object across async boundaries | `PythonExecutor.shared.store()` → `PyHandle` (ch3) |
| Auto-release a remote Python object on scope exit | `pool.evalOwned()` → `OwnedPyHandle` (ch3, v0.2.0+) |
| Execute Python across multiple CPU cores | `PythonProcessPool` → `eval`/`invoke`/`method` (ch4) |
| Iterate a Python generator over IPC | `pool.evalStream<T>(options:)` → `CancellableStream` (ch5) |
| Iterate a Python generator with progress events | `pool.evalEventStream<T>(options:)` → `CancellableStream<StreamEvent<T>>` (ch5, v0.2.0+) |
| Subscribe to pool worker lifecycle (spawns, deaths, orphaned callbacks) | `pool.events()` → `AsyncStream<PoolEvent>` (ch4, v0.2.0+) |
| Run parallel tasks with dependencies | `ProcessPoolDAG` (ch6) |
| Let Python call a Swift function | `pool.registerCallback` / `registerReentrantCallback` (ch7) |
| Call NumPy / Pandas / MLX etc. with type safety | Generated module APIs (ch8) |
