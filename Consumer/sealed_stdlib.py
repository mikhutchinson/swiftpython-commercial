#!/usr/bin/env python3
"""Audit producer-sealed CPython stdlib bytecode without executing its source.

This producer/release gate needs the matching CPython 3.13 build interpreter.
Ordinary consumers verify the publisher payload hashes, not installed Python.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import marshal
from pathlib import Path
import sys
import struct
import types

sys.dont_write_bytecode = True

from contract import ContractError, require, sha256


TRACEBACK_ROOT = "/__swiftpython__/python3.13"


def same_compiled_value(actual, expected):
    """Compare every code field; marshal's reference sharing is not semantics."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, types.CodeType):
        fields = ("co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals", "co_stacksize",
                  "co_flags", "co_code", "co_consts", "co_names", "co_varnames", "co_filename", "co_name",
                  "co_qualname", "co_firstlineno", "co_linetable", "co_exceptiontable", "co_freevars", "co_cellvars")
        return all(same_compiled_value(getattr(actual, field), getattr(expected, field)) for field in fields)
    if isinstance(expected, tuple):
        return len(actual) == len(expected) and all(same_compiled_value(a, b) for a, b in zip(actual, expected))
    if isinstance(expected, frozenset):
        return len(actual) == len(expected) and all(any(same_compiled_value(a, b) for a in actual) for b in expected)
    if isinstance(expected, float):
        return struct.pack(">d", actual) == struct.pack(">d", expected)
    if isinstance(expected, complex):
        return same_compiled_value(actual.real, expected.real) and same_compiled_value(actual.imag, expected.imag)
    return actual == expected


def audit_framework(framework):
    require(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 13),
            "Sealed stdlib verification requires the matching CPython 3.13 build interpreter")
    framework = Path(framework).resolve(strict=True)
    stdlib = framework / "Versions/3.13/lib/python3.13"
    require(stdlib.is_dir() and stdlib.resolve() == stdlib, "Missing regular packaged standard library")
    expected = {}
    for source in sorted(stdlib.rglob("*.py")):
        relative = source.relative_to(stdlib)
        require(not source.is_symlink() and "site-packages" not in relative.parts,
                f"Unreviewed package or symlink in sealed stdlib: {relative}")
        cache = source.parent / "__pycache__" / (source.stem + ".cpython-313.pyc")
        raw = source.read_bytes()
        code = compile(raw, TRACEBACK_ROOT + "/" + relative.as_posix(), "exec", dont_inherit=True, optimize=0)
        header = importlib.util.MAGIC_NUMBER + (1).to_bytes(4, "little") + importlib.util.source_hash(raw)
        require(cache.is_file() and not cache.is_symlink(), f"Missing sealed bytecode: {cache}")
        content = cache.read_bytes()
        require(content[:16] == header,
                f"Bytecode is not the exact source-derived unchecked-hash product: {cache}")
        stream = io.BytesIO(content[16:])
        try:
            observed = marshal.load(stream)
        except (ValueError, EOFError, TypeError) as error:
            raise ContractError(f"Malformed sealed bytecode: {cache}") from error
        require(stream.read() == b"" and same_compiled_value(observed, code),
                f"Bytecode is not the exact source-derived unchecked-hash product: {cache}")
        expected[str(cache.relative_to(framework))] = sha256(cache)
    require(expected, "No standard-library source/cache pairs")
    actual = {str(path.relative_to(framework)) for path in framework.rglob("*.pyc")}
    require(actual == set(expected), "Extra or missing packaged bytecode")
    expected_directories = {str(Path(path).parent) for path in expected}
    directories = {str(path.relative_to(framework)) for path in framework.rglob("__pycache__")}
    require(directories == expected_directories, "Unexpected packaged cache directory")
    return {"schemaVersion": 1, "scope": "source-derived-sealed-stdlib", "passed": True,
            "pythonVersion": sys.version.split()[0], "tracebackRoot": TRACEBACK_ROOT,
            "invalidationMode": "unchecked-hash", "optimization": 0, "files": expected}


def distribution_bytecode_paths(root):
    """Return only validated, payload-attested cache entries for a tree audit."""
    from release_manifest import verified_payload
    root = Path(root).resolve(strict=True)
    verified_payload(root, allow_development=True)
    paths = set()
    for framework in sorted((root / "Python.xcframework").glob("*/Python.framework")):
        if not any(framework.rglob("__pycache__")) and not any(framework.rglob("*.pyc")):
            continue  # Source-only historical runtime payloads remain valid.
        result = audit_framework(framework)
        for entry in result["files"]:
            relative = (framework / entry).relative_to(root).as_posix()
            paths.add(relative)
            paths.add(Path(relative).parent.as_posix())
    return paths


def audit_app_bytecode(app, distribution):
    app, distribution = Path(app).resolve(strict=True), Path(distribution).resolve(strict=True)
    expected = set()
    for relative in distribution_bytecode_paths(distribution):
        source = distribution / relative
        _, tail = relative.split("/Python.framework/", 1)
        installed = app / "Contents/Frameworks/Python.framework" / tail
        expected.add(installed.relative_to(app).as_posix())
        if source.is_file():
            require(sha256(installed) == sha256(source), f"Changed sealed app bytecode: {installed}")
        else:
            require(installed.is_dir() and not installed.is_symlink(), f"Missing sealed app cache: {installed}")
    actual = {path.relative_to(app).as_posix() for path in app.rglob("*")
              if path.name == "__pycache__" or path.suffix == ".pyc"}
    require(actual == expected, "App acquired unapproved or missing bytecode after assembly")
    return {"passed": True, "scope": "publisher-matched-app-bytecode", "entries": len(expected)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--framework", type=Path)
    mode.add_argument("--distribution", type=Path)
    parser.add_argument("--app", type=Path, help="With --distribution, verify an assembled app's exact cache set")
    try:
        args = parser.parse_args()
        require(not args.app or args.distribution, "--app requires --distribution")
        if args.app:
            print(json.dumps(audit_app_bytecode(args.app, args.distribution), sort_keys=True))
        elif args.distribution:
            print(json.dumps(sorted(distribution_bytecode_paths(args.distribution))))
        else:
            result = audit_framework(args.framework)
            print(json.dumps({key: value for key, value in result.items() if key != "files"} |
                             {"bytecodeFiles": len(result["files"])}, sort_keys=True))
    except (ContractError, OSError, ValueError, SyntaxError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
