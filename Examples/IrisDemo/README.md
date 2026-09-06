# Iris

Explore Iris, Wine and Breast Cancer datasets in a native Mac app. Select a
chart point or table row to inspect its measurements. Train a classifier,
filter its held-out mistakes, and adjust two features to request a fresh
prediction from the fitted model. Original samples and test scores stay fixed.

## Run

```bash
Examples/IrisDemo/scripts/build_app.sh --open
```

Run from the commercial repository root. The builder creates
`Examples/IrisDemo/build/IRIS.app`, embedding the matched runtime and worker.
It downloads hash-locked NumPy, SciPy, scikit-learn, joblib and threadpoolctl
wheels during the build and signs their native extensions before sealing the
app. Running the app needs no Homebrew, system Python, Python packages or
runtime downloads. Building requires the Xcode command-line tools and network
access for uncached artifacts.

The app is ad hoc signed for development. Use the root
[distribution guide](../../README.md) for Developer ID signing, notarization
and sandbox deployment. Wheel license notices and metadata are retained under
`Contents/Resources/PythonPackages`.

## Implementation

[`IrisKernel`](Sources/Services/IrisKernel.swift) owns one PythonProcessPool
worker. It imports [`iris_kernel.py`](Sources/Python/iris_kernel.py) once.
Python caches the three datasets and retains only the current fitted model.
Swift receives typed dataset, training and prediction records; no Python objects
enter SwiftUI. Model identities and UI request revisions reject stale results.
Prediction input is coalesced to one active call plus one replaceable pending
value. Application termination awaits worker shutdown.

Each classifier uses the same deterministic, stratified 75/25 train/test split.
Cross-validation and learning curves use training rows only. Scaling is fitted
inside each fold's Pipeline. The inspector labels original held-out predictions
and edited-value experiments separately.

## Verify

```bash
swift test --package-path Examples/IrisDemo --disable-swift-testing --filter IrisChartTests
Examples/IrisDemo/build/IRIS.app/Contents/MacOS/IrisDemo --smoke /tmp/iris-receipt.json
codesign --verify --deep --strict Examples/IrisDemo/build/IRIS.app
```

The chart tests render the SwiftUI view while selecting points, showing mistakes,
and replacing datasets with different feature and class counts. Chart marks and
hit testing capture the same input values, so a pending render cannot mix old
samples with new axes or class labels.

The smoke command exercises all nine dataset/classifier combinations, compares
retained-model predictions with the original test predictions, verifies test
cohort identities, and checks that the owned worker was reaped. It writes a
JSON receipt and exits. Python tests in `Tests/test_iris_kernel.py` cover held-out
separation, cached data, stale models, invalid values and immutable originals;
run them with a Python environment containing the pinned numerical packages.

Native layout and controls follow [macOS HIG](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos),
[HIG foundations](https://developer.apple.com/design/human-interface-guidelines/foundations/),
and [accessibility guidance](https://developer.apple.com/design/human-interface-guidelines/accessibility).
