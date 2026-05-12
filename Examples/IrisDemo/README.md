# IRIS

IRIS is a complete macOS SwiftUI example for the public SwiftPython runtime
binary distribution. It loads sklearn datasets, renders them with Swift Charts,
and trains classifiers through a bundled Python service module.

## Run

```bash
cd Examples/IrisDemo
./scripts/build_app.sh --open
```

The script builds the Swift package, creates `build/IRIS.app`, copies the
bundled Python resources into the app, and launches the app when `--open` is
passed.

## Boundary

Swift owns the UI and view model. Python owns the sklearn data/model work in
`Sources/Python/iris_kernel.py`. The Swift facade in
`Sources/Services/IrisKernel.swift` loads that file as a bundled resource and
exchanges JSON payloads with it through `SwiftPythonRuntime`.
