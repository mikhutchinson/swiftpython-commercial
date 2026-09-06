#!/usr/bin/env python3
"""Bundle hash-locked wheels at build time; never execute package installers."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import urllib.request
import zipfile


def vendor(lock_path, output, cache, architecture, packages):
    lock = json.loads(lock_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    receipt = {"python": lock["python"], "architecture": architecture, "wheels": []}
    for package in packages:
        wheel = lock["packages"][package]["wheels"][architecture]
        archive = cache / wheel["filename"]
        if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != wheel["sha256"]:
            temporary = archive.with_suffix(".download")
            try:
                with urllib.request.urlopen(wheel["url"], timeout=60) as source, temporary.open("wb") as target:
                    shutil.copyfileobj(source, target)
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != wheel["sha256"]:
                    raise RuntimeError(f"Wheel hash mismatch: {wheel['filename']}")
                temporary.replace(archive)
            finally:
                temporary.unlink(missing_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or stat.S_ISLNK(entry.external_attr >> 16):
                    raise RuntimeError(f"Unsafe wheel member: {entry.filename}")
                if entry.is_dir():
                    continue
                # Wheel install schemes may carry importable files in .data.
                # These demos need no installed scripts or external data paths.
                if path.parts[0].endswith(".data"):
                    if len(path.parts) < 3 or path.parts[1] not in ("purelib", "platlib"):
                        raise RuntimeError(f"Unsupported wheel install scheme: {entry.filename}")
                    path = PurePosixPath(*path.parts[2:])
                destination = output.joinpath(*path.parts)
                if destination.exists():
                    raise RuntimeError(f"Overlapping wheel member: {entry.filename}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                os.chmod(destination, 0o755 if destination.suffix in (".so", ".dylib") else 0o644)
        receipt["wheels"].append({"package": package, **wheel})
        print(f"Bundled {package} {lock['packages'][package]['version']} ({architecture})", flush=True)
    (output / "swiftpython-demo-wheels.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("packages", nargs="+")
    args = parser.parse_args()
    vendor(args.lock, args.output, args.cache, args.arch, args.packages)
