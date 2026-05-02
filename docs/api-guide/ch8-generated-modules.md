# Chapter 8 — Generated Modules

Generated modules are type-safe Swift APIs produced from Python `.pyi` stubs. Never hand-edit them — regenerate from stubs instead.

## Available modules

| Swift target | Python module | Key examples |
|---|---|---|
| `NumPy` | `numpy` | `example_numpy.swift`, `example_slicing.swift` |
| `Pandas` | `pandas` | `example_pandas.swift`, `example_async.swift` |
| `SciPy` | `scipy` | `example_scipy.swift` |
| `Sklearn` | `sklearn` | `example_sklearn.swift`, `example_sklearn_advanced.swift` |
| `SQLite3` | `sqlite3` | `example_sqlite3.swift` |
| `OpenCV` | `cv2` | `example_opencv.swift` |
| `PIL` | `PIL` | (used transitively in vision examples) |
| `Matplotlib` | `matplotlib` | `example_matplotlib.swift` |
| `PyTorch` | `torch` | `example_pytorch.swift`, `example_image_classification.swift` |
| `Torchvision` | `torchvision` | `example_image_classification.swift` |
| `Transformers` | `swiftpython_transformers` | `example_transformers.swift`, `example_transformers_local.swift` |
| `MLX` | `mlx` | `example_mlx.swift`, `example_mlx_dag.swift` |
| `NetworkX` | `networkx` | `example_networkx.swift` |
| `LlamaCpp` | `llama_cpp` | `example_llamacpp.swift` |

Optional modules (skip gracefully if Python package missing): `PyTorch`, `Torchvision`, `MLX`, `Transformers`, `LlamaCpp`.

## API shape conventions

All generated modules follow the same pattern:

```swift
// Module-level enum namespace
NumPy.linspace(start: 0.0, stop: 1.0, num: 100)
Pandas.read_csv(filepath_or_buffer: "/data/file.csv")

// Generated wrapper structs for Python classes
let arr: ndarray = try await NumPy.zeros([100, 100])
let df: DataFrame = try await Pandas.read_csv(filepath_or_buffer: "/data/file.csv")

// Async methods mirroring Python methods
let mean: Double = try await arr.mean()
let shaped: ndarray = try await arr.reshape([10, 10])
```

Optional/keyword arguments map to Swift optionals:

```swift
// Python: numpy.linspace(start, stop, num=50, endpoint=True, ...)
NumPy.linspace(start: 0.0, stop: 1.0)            // num defaults to nil
NumPy.linspace(start: 0.0, stop: 1.0, num: 200)  // explicit
```

Variadic args/kwargs passthrough:

```swift
func someFunc(_ args: [any PythonConvertible] = [], extraKwargs: [String: any PythonConvertible] = [:])
```

## Remote (ProcessPool) wrappers — `rNumPy`, `rPandas`, etc.

Generated with `--remote`, these wrap the same Python APIs but route through `PythonProcessPool`:

```swift
// Module-level remote call
let h: PyHandle = try await rNumPy.zeros(pool, [1000, 1000])

// Remote wrapper type — stays on the worker
let remote: RemoteNDArray = ...
let result: [Double] = try await remote.tolist(pool)

// Worker-bound
let ctx = pool.worker(1)
let h = try await rNumPy.eye(ctx, 4)
```

`rNDArray` is a convenience alias for `RemoteNDArray`.

## Escape hatch for missing bindings

When a Python API has no generated wrapper:

```swift
let result: ndarray = try await Python.run {
    let np = try Python.import("numpy")
    let rng = try np.random.default_rng(42)
    return ndarray(pythonObject: try rng.standard_normal([100, 4]))
}
```

Rule: keep dynamic access local; re-wrap into the typed wrapper at the boundary.

## Transformers — special case

Transformers is generated in-place against an internal shim module (`swiftpython_transformers`). No `PYTHONPATH` setup required — the shim is bundled as a runtime resource.

```swift
// Load a pipeline
let pipeline = try await Transformers.pipeline(task: "text-classification")

// Classify
let predictions: [[TextClassificationPrediction]] = try await pipeline.classify(
    inputs: ["I love this!", "This is terrible."]
)
// predictions[0][0].label, .score

// Tokenization boundary report
let report: TokenizationBoundaryReport = try await pipeline.tokenizationBoundaryReport(
    inputs: texts, maxLength: 512
)
```

Value types: `TextClassificationPrediction`, `TokenizationBoundaryRow`, `TokenizationBoundaryReport`.

## LlamaCpp — value-type structs

LlamaCpp generates 8 value-type Swift structs decoded from Python dicts:

`CompletionResult`, `ChatCompletionResult`, `CompletionUsage`, `CompletionChoice`,
`ChatCompletionChoice`, `ChatMessage`, `EmbeddingResult`, `EmbeddingData`

```swift
let model = try await LlamaCpp.Llama(model_path: "/models/llama.gguf")
let result: CompletionResult = try await model.createCompletion(prompt: "Hello")
print(result.choices[0].text)
```

## Regeneration workflow

```bash
# Single module
swift run swift-python-gen --module NumPy --stub-dir stubs/numpy --output Sources/NumPy

# Custom Python module name (e.g. PyTorch uses 'torch')
swift run swift-python-gen --module PyTorch --python-module torch --stub-dir stubs/torch --output Sources/PyTorch

# Remote (ProcessPool) wrappers
swift run swift-python-gen --module NumPy --python-module numpy --stub-dir stubs/numpy --output Sources/NumPy/Remote --remote
```

Stubs live in `stubs/<package>/`. See the stub generation guide for stub authoring.

**CI regenerates all bindings on every push and fails if there's a diff.** Always commit regenerated files.

## Stub-directed value types

Mark a class in the stub with `# swiftpython: value-type` to generate a Swift struct instead of a wrapper class:

```python
class MyResult:  # swiftpython: value-type
    score: float
    label: str
```

Generates `struct MyResult: Sendable, PythonConvertible` — decoded via dict keys, encoded via constructor kwargs.
