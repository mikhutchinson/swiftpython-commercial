# Core Runtime Smoke

Minimal macOS CLI that exercises **in-process** `Python.run` against the bundled
SwiftPython runtime. This matches the README “Smoke Test” snippet: read
`sys.version`, then serialize a tiny object with the standard-library `json`
module.

## Run

From this directory:

```bash
swift run
```

The manifest auto-detects Homebrew's Apple Silicon and Intel Python 3.13
prefixes. For custom Python layouts, set `PYTHON_HOME`, `PYTHONHOME`, or
`SWIFTPYTHON_PYTHON_LIB_DIR`.

## What it proves

- The package links against `Python.framework` / `libpython` as documented.
- The embedded interpreter imports `sys` and `json` successfully from Swift.
