# Chapter 6 — DAG Orchestration

`ProcessPoolDAG` schedules dependency-aware work across pool workers. Nodes run as soon as all their dependencies complete; independent nodes run in parallel.

## Basic usage

```swift
let dag = ProcessPoolDAG(nodes: [
    .init(id: "load") { ctx in
        try await ctx.worker.evalResult("load_data()")
    },
    .init(id: "preprocess", dependencies: ["load"]) { ctx in
        let raw: [Double] = try ctx.result(for: "load")
        return try await ctx.worker.evalResult("preprocess(\(raw))")
    },
    .init(id: "train", dependencies: ["preprocess"]) { ctx in
        let data: [Double] = try ctx.result(for: "preprocess")
        return try await ctx.worker.evalResult("train(\(data))")
    },
])

let results: [String: [Double]] = try await pool.run(dag)
```

## Node options

```swift
ProcessPoolDAG.Node(
    id: "train",
    dependencies: ["preprocess"],
    preferredWorker: 2,         // optional — pin to a specific worker
    operation: { ctx in ... }
)
```

## `Context` inside a node

```swift
public struct ProcessPoolDAG.Context {
    public let pool: PythonProcessPool
    public let workerIndex: Int
    public var worker: PythonProcessPool.WorkerContext   // bound to workerIndex
    public var results: [NodeID: Output]
    public func result(for dependency: NodeID) throws -> Output
}
```

Use `ctx.worker` for all IPC calls within a node — it's already pinned to the assigned worker.

## Failure policies

### `failFast` (default) — cancel everything on first error

```swift
let results = try await pool.run(dag)
// throws on first node failure; other nodes are cancelled
```

### `continueIndependent` — isolate failures

```swift
let dagResult: DAGResult<String, [Double]> = try await pool.run(
    dag, failurePolicy: .continueIndependent
)

dagResult.completed   // [NodeID: Output] — succeeded nodes
dagResult.failed      // [NodeID: Error]  — failed nodes
dagResult.skipped     // Set<NodeID>      — dependents of failed nodes
dagResult.allSucceeded
```

## Max parallelism

```swift
let results = try await pool.run(dag, maxParallelism: 2)
// At most 2 nodes run concurrently regardless of available workers
```

## Inline nodes convenience

```swift
let results = try await pool.run(nodes: [
    .init(id: "a") { ctx in ... },
    .init(id: "b", dependencies: ["a"]) { ctx in ... },
])
```

## Validation errors

`ProcessPoolDAGError` is thrown at `pool.run` time (before any work starts):

```swift
case duplicateNodeID(String)
case missingDependency(node: String, dependency: String)
case cycleDetected
case invalidMaxParallelism(Int)
case invalidWorkerIndex(index: Int, validRange: Range<Int>)
case missingDependencyResult(String)
case internalInvariantViolation(String)
```

## Examples

- `example_mlx_dag.swift` — MLX training pipeline
- `example_shared_dag.swift` — shared-memory DAG with zero-copy NumPy
- Tests: `ProcessPoolDAGTests.swift`
