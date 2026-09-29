#!/usr/bin/env python3
"""Shared, source-independent input inventory for commercial worker consumers.

Publisher hashes describe inputs before consumer signing. A local inventory
detects drift, but release provenance additionally requires the external release
manifest's hash of this inventory. This module never signs or repairs inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def exact_value(left, right):
    """JSON contract equality without Python's True == 1 or 1.0 == 1 coercion."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(exact_value(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(exact_value(a, b) for a, b in zip(left, right))
    return left == right


def relative_path(value):
    require(isinstance(value, str) and value and "\\" not in value,
            "Expected a nonempty POSIX relative path")
    parts = value.split("/")
    require(not value.startswith("/") and all(p not in ("", ".", "..") for p in parts),
            f"Unsafe relative path: {value!r}")
    return PurePosixPath(value)


def sha256(path):
    path = Path(path)
    require(stat.S_ISREG(path.lstat().st_mode), f"Expected a regular file: {path}")
    digest = hashlib.sha256()
    # Refuse substitution with a symlink between lstat and open. Callers still
    # keep verified inputs immutable throughout assembly to prevent later drift.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), f"Expected a regular file: {path}")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    path = Path(path)
    require(stat.S_ISREG(path.lstat().st_mode), f"Expected regular JSON: {path}")
    require(path.stat().st_size <= 32 * 1024 * 1024, f"Oversized JSON: {path}")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=unique,
                         parse_constant=lambda value: require(False, f"Non-JSON numeric constant: {value}"))


def tree_inventory(root, roots):
    """Inventory complete selected trees, including empty dirs and symlinks.

    Paths are relative to the artifact root. Symlinks must be relative, resolve
    within their selected tree, and name an existing object. They are recorded,
    never followed during enumeration; framework Versions/Current is preserved.
    """
    root = Path(root).resolve(strict=True)
    require(isinstance(roots, list) and roots and len(set(roots)) == len(roots),
            "Inventory roots must be a nonempty unique list")
    selected = [relative_path(value) for value in roots]
    for index, path in enumerate(selected):
        require(all(path not in other.parents and other not in path.parents
                    for other in selected[index + 1:]), "Overlapping inventory roots")
    result = {}

    def visit(path, boundary):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            require(not os.path.isabs(target), f"Absolute symlink: {relative}")
            try:
                destination = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ContractError(f"Broken or cyclic symlink: {relative}") from error
            require(destination == boundary or boundary in destination.parents,
                    f"Escaping symlink: {relative}")
            result[relative] = {"kind": "symlink", "target": target}
        elif stat.S_ISDIR(metadata.st_mode):
            result[relative] = {"kind": "directory", "mode": mode}
            for child in sorted(path.iterdir()):
                visit(child, boundary)
        elif stat.S_ISREG(metadata.st_mode):
            result[relative] = {"kind": "file", "mode": mode,
                                "size": metadata.st_size, "sha256": sha256(path)}
        else:
            raise ContractError(f"Special file is not a release input: {relative}")

    for relative in selected:
        path = root.joinpath(*relative.parts)
        require(not path.is_symlink(), f"Inventory root must not be a symlink: {relative}")
        require(path.resolve(strict=True).is_relative_to(root), f"Escaping root: {relative}")
        visit(path, path.resolve(strict=True) if path.is_dir() else root)
    return result


def verify_inventory(root, inventory):
    require(type(inventory) is dict and set(inventory) == {"roots", "entries"},
            "Unsupported input inventory shape")
    require(type(inventory["entries"]) is dict, "Inventory entries must be an object")
    actual = tree_inventory(root, inventory["roots"])
    expected = inventory["entries"]
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(key for key in set(actual) & set(expected) if not exact_value(actual[key], expected[key]))
    require(not (missing or extra or changed),
            f"Input inventory mismatch: missing={missing[:5]}, extra={extra[:5]}, changed={changed[:5]}")
    return actual
