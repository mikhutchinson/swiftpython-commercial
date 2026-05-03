# Chapter 6 - DAG Orchestration

`ProcessPoolDAG` runs dependency-aware Swift operations over a
`PythonProcessPool`. Use it when your work is naturally a graph: load data,
preprocess, fan out independent work, then reduce results.

The DAG scheduler controls task ordering and parallelism. Your node operations
still use normal pool APIs.

## Basic DAG

```swift
let dag = ProcessPoolDAG<String, PyHandle>(nodes: [
    .init(id: "raw") { ctx in
        try await ctx.worker.invoke(
            module: "my_pipeline",
            function: "load_raw",
            args: [.python("/data/input.csv")]
        )
    },
    .init(id: "features", dependencies: ["raw"]) { ctx in
        let raw = try ctx.result(for: "raw")
        return try await ctx.worker.invoke(
            module: "my_pipeline",
            function: "build_features",
            args: [.handle(raw)]
        )
    },
    .init(id: "model", dependencies: ["features"]) { ctx in
        let features = try ctx.result(for: "features")
        return try await ctx.worker.invoke(
            module: "my_pipeline",
            function: "train_model",
            args: [.handle(features)]
        )
    },
])

let results = try await pool.run(dag)
let model = results["model"]!
```

`ctx.worker` is already pinned to the worker chosen for that node. Prefer it
inside node operations so related calls stay worker-affine.

## Node Shape

```swift
enum PipelineValue: Sendable {
    case handle(PyHandle)
    case score(Double)
    case report(String)
}

ProcessPoolDAG<String, PipelineValue>.Node(
    id: "score",
    dependencies: ["model", "validation"],
    preferredWorker: 1
) { ctx in
    guard case .handle(let model) = try ctx.result(for: "model"),
          case .handle(let validation) = try ctx.result(for: "validation") else {
        throw PipelineError.invalidDependency
    }

    let score: Double = try await ctx.worker.invokeResult(
        module: "my_pipeline",
        function: "score",
        args: [.handle(model), .handle(validation)]
    )

    return .score(score)
}
```

All nodes in a DAG share one output type. Use a small enum for mixed outputs.

For a single-purpose DAG, the output can be a simple type:

```swift
let scores = ProcessPoolDAG<String, Double>(nodes: [
    .init(id: "score-a") { ctx in
    return try await ctx.worker.invokeResult(
        module: "my_pipeline",
        function: "score_file",
        args: [.python("/data/a.json")]
    )
    }
])
```

| Field | Meaning |
|-------|---------|
| `id` | Unique node identifier |
| `dependencies` | Nodes that must complete before this one starts |
| `preferredWorker` | Optional worker index preference |
| `operation` | Async Swift closure that returns the node output |

## Running

```swift
let outputs: [String: PipelineValue] = try await pool.run(dag)
```

Limit parallelism when the graph is wider than your resource budget:

```swift
let outputs = try await pool.run(dag, maxParallelism: 2)
```

You can also skip constructing the wrapper explicitly:

```swift
let outputs = try await pool.run(nodes: [
    .init(id: "a") { ctx in ... },
    .init(id: "b", dependencies: ["a"]) { ctx in ... },
])
```

## Failure Policies

Default behavior is fail-fast:

```swift
let outputs = try await pool.run(dag)
```

If any node fails, the run throws and dependent work is cancelled or skipped.

Use `continueIndependent` when independent branches should keep running:

```swift
let result: DAGResult<String, PipelineValue> = try await pool.run(
    dag,
    failurePolicy: .continueIndependent
)

print(result.completed.keys)
print(result.failed.keys)
print(result.skipped)
```

`DAGResult` separates completed, failed, and skipped nodes and exposes
`allSucceeded`.

## Validation Errors

The scheduler validates the graph before it starts work.

| Error | Meaning |
|-------|---------|
| `duplicateNodeID` | Two nodes share the same id |
| `missingDependency` | A dependency id is not present in the DAG |
| `cycleDetected` | Dependencies contain a cycle |
| `invalidMaxParallelism` | `maxParallelism` is not usable |
| `invalidWorkerIndex` | A preferred worker is outside the pool range |
| `missingDependencyResult` | A node asked for a result that is unavailable |

## Practical Pattern

Keep Python code in importable modules and call it from nodes. This gives you:

- cleaner Swift DAG code,
- reusable Python implementation,
- smaller IPC payloads,
- easier crash/error diagnosis.

```swift
let summarize = ProcessPoolDAG<String, String>.Node(
    id: "summary",
    dependencies: ["document"]
) { ctx in
    let document = try ctx.result(for: "document")
    return try await ctx.worker.invokeResult(
        module: "my_docs",
        function: "summarize",
        args: [.python(document)]
    )
}
```

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Interpolating large Swift arrays into code strings | Pass values as arguments or handles |
| Mixed node output types | Use a small `Sendable` enum |
| Node uses `pool` instead of `ctx.worker` | Use `ctx.worker` when worker affinity matters |
| Failure in one branch cancels too much work | Use `.continueIndependent` |
| DAG grows into business logic soup | Keep business logic in Python/Swift modules and use the DAG for orchestration |
