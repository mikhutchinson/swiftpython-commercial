# Core Runtime Smoke

Minimal macOS CLI that exercises **in-process** `Python.run` against the bundled
SwiftPython runtime. This matches the README “Smoke Test” snippet: read
`sys.version`, then serialize a tiny object with the standard-library `json`
module.

## Run

From this directory (with Python 3.13 available via the same linker path as other examples):

```bash
swift run
```

## What it proves

- The package links against `Python.framework` / `libpython` as documented.
- The embedded interpreter imports `sys` and `json` successfully from Swift.
