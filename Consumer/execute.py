#!/usr/bin/env python3
"""Verify a fresh minimal integration run through normal macOS app launch.

Exact binary paths and kernel process start times constrain observation and
cleanup. A successful smoke is distinct from the full release corpus.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True

from assemble import load_project, preflight, write_json
from contract import ContractError, load_json, require, sha256
from native import command


class BSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
    _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "jobc", "tdev", "tpgid")]
    _fields_ += [("nice", ctypes.c_int32), ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]


def contained_runtime(prefix, app):
    require(isinstance(prefix, str) and Path(prefix).is_absolute(), "Expected an absolute Python runtime path")
    actual = Path(prefix).resolve(strict=True)
    parent = Path(app).resolve(strict=True)
    require(actual != parent and actual.is_relative_to(parent), "Python runtime is outside the signed app")
    return actual


def registered_app_paths(registry_text, bundle_identifier):
    """Select only this consumer's records from LaunchServices diagnostic text."""
    paths = set()
    for record in re.split(r"(?m)^-{5,}\s*$", registry_text):
        identifier = re.search(r"(?m)^identifier:\s*(\S+)\s*$", record)
        if identifier is None or identifier[1] != bundle_identifier:
            continue
        path = re.search(r"(?m)^path:\s*(.+?)\s+\(0x[0-9A-Fa-f]+\)\s*$", record)
        require(path is not None and Path(path[1]).is_absolute(),
                "Could not establish the registered consumer's absolute app path")
        paths.add(Path(path[1]).resolve())
    return paths


def require_unique_registration(app, bundle_identifier):
    tool = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    paths = registered_app_paths(command(tool, "-dump"), bundle_identifier)
    collisions = sorted(str(path) for path in paths if path != app and path.exists())
    require(not collisions, "Another registered app uses this consumer identity: " + repr(collisions) +
            ". Use a unique bundle identifier, or explicitly unregister a superseded development copy before launch.")
    return sorted(str(path) for path in paths if path.exists())


def verify_source_denials(receipt, paths):
    require(type(receipt) is dict and set(receipt) == {"host", "worker"}, "Missing source-denial evidence")
    expected = {str(path) for path in paths}
    for owner in ("host", "worker"):
        observations = receipt[owner]
        require(type(observations) is list and len(observations) == len(expected), f"Incomplete {owner} denial probes")
        seen = set()
        for observation in observations:
            require(type(observation) is dict and set(observation) == {"path", "denied", "errno"} and
                    observation["denied"] is True and type(observation["errno"]) is int and
                    observation["errno"] in (errno.EACCES, errno.EPERM), f"Source was not permission-denied to {owner}")
            require(observation["path"] in expected and observation["path"] not in seen, f"Unknown or duplicate {owner} source probe")
            seen.add(observation["path"])


def receipt_from_console(text):
    records = re.findall(r"(?m)^SWIFTPYTHON_CONSUMER_RECEIPT ([A-Za-z0-9+/=]+)$", text)
    require(len(records) == 1, "Expected exactly one structured consumer result on the owned stdout route")
    return base64.b64decode(records[0], validate=True)


def preserve_input_receipts(root, signing_path, output, preflight_result):
    """Incremental builds must not erase the provenance of an earlier run."""
    signing = load_json(signing_path)
    sources = {
        "signing-receipt.json": (signing_path, preflight_result["signingReceiptSHA256"]),
        "assembly.json": (root / "assembly.json", signing["assemblySHA256"]),
        "payload.json": (root / "Inputs/payload.json", signing["payloadSHA256"]),
    }
    for name, (source, expected) in sources.items():
        data = Path(source).read_bytes()
        require(hashlib.sha256(data).hexdigest() == expected, f"Changed execution provenance: {source}")
        (output / name).write_bytes(data)


class ProcessObserver:
    def __init__(self, app):
        self.app = app
        self.lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        self.lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        self.lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        self.owned = {}
        self.seen = {}

    def identity(self, pid):
        value = BSDInfo()
        if self.lib.proc_pidinfo(int(pid), 3, 0, ctypes.byref(value), ctypes.sizeof(value)) != ctypes.sizeof(value):
            return None
        return {"ppid": str(value.ppid), "start": [value.start_sec, value.start_usec]}

    def sample(self):
        pids = (ctypes.c_int * 65536)()
        count = self.lib.proc_listallpids(pids, ctypes.sizeof(pids))
        require(0 <= count < len(pids), "Could not obtain a complete process snapshot")
        table, exact = {}, {}
        for pid in pids[:count]:
            identity = self.identity(pid)
            if identity is None:
                continue
            table[str(pid)] = identity
            buffer = ctypes.create_string_buffer(4096)
            if self.lib.proc_pidpath(pid, buffer, len(buffer)) > 0:
                path = buffer.value.decode("utf-8", "replace")
                if path.startswith(str(self.app) + "/"):
                    exact[str(pid)] = path
        admitted = set(exact) | {pid for pid, identity in self.owned.items()
                                if table.get(pid, {}).get("start") == identity["start"]}
        while True:
            added = {pid for pid, identity in table.items() if identity["ppid"] in admitted} - admitted
            if not added:
                break
            admitted.update(added)
        self.owned.update({pid: table[pid] for pid in admitted if pid in table})
        self.seen.update(exact)
        return exact

    def survivors(self):
        return {pid: value for pid, value in self.owned.items()
                if (self.identity(pid) or {}).get("start") == value["start"]}

    def cleanup(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid, identity in self.survivors().items():
                if (self.identity(pid) or {}).get("start") == identity["start"]:
                    try:
                        os.kill(int(pid), sig)
                    except ProcessLookupError:
                        pass
            deadline = time.monotonic() + 2
            while self.survivors() and time.monotonic() < deadline:
                time.sleep(0.025)


def verify(args):
    root, assembly = load_project(args.project)
    before = preflight(root, args.receipt)
    app = Path(before["app"]).resolve(strict=True)
    require(1 <= args.timeout <= 600, "Execution timeout must be 1 through 600 seconds")
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(app), "Use a new evidence directory outside the app")
    config = assembly["configuration"]
    probes = [path.resolve(strict=True) for path in args.deny_source]
    require(len(probes) == len(set(probes)) and len(probes) <= 16, "Use at most 16 unique source-denial probes")
    if probes:
        require(config["parentSandbox"] and config["serviceSandbox"],
                "Source-denial qualification requires independently sandboxed parent and workers")
        for path in probes:
            require(path.is_file() and not path.is_relative_to(app), "Probe an existing private source file outside the app")
            # Prove the file exists and is readable to the runner. ENOENT is
            # never accepted as evidence of sandbox enforcement in the app.
            with path.open("rb") as stream:
                stream.read(1)
    registrations = require_unique_registration(app, config["bundleIdentifier"])
    observer = ProcessObserver(app)
    require(not observer.sample(), "This exact application already has running processes")
    output.mkdir(parents=True)
    preserve_input_receipts(root, args.receipt, output, before)
    run_id = str(uuid.uuid4())
    started = time.time()
    deadline = time.monotonic() + args.timeout
    launch_arguments = ["open", "-n", "-W", "--stdout", str(output / "console.log"),
                        "--stderr", str(output / "stderr.log"), str(app),
                        "--args", "--stdout-evidence", "--run-id", run_id]
    for path in probes:
        launch_arguments += ["--deny-source", str(path)]
    launch = subprocess.Popen(launch_arguments)
    samples, result = [], {"stage": "execution", "passed": False, "runID": run_id,
                          "scope": "minimal-integration", "preflight": before,
                          "registeredAppPaths": registrations, "startedAtUnixSeconds": started}
    try:
        while launch.poll() is None:
            current = observer.sample()
            if current:
                samples.append({"seconds": round(time.time() - started, 3), "processes": current})
            require(time.monotonic() < deadline, "Consumer exceeded its execution deadline")
            time.sleep(0.025)
        require(launch.returncode == 0, "LaunchServices failed to launch the exact application")
        console = output / "console.log"
        require(not console.is_symlink() and console.stat().st_mtime >= started, "Missing fresh owned stdout evidence")
        (output / "receipt.json").write_bytes(receipt_from_console(console.read_text()))
        receipt = load_json(output / "receipt.json")
        require(receipt.get("runID") == run_id and receipt.get("passed") is True and
                receipt.get("completedShutdown") is True, f"Consumer smoke failed: {receipt}")
        require(receipt.get("appPath") == str(app) and receipt.get("bundleIdentifier") == config["bundleIdentifier"] and
                receipt.get("host") == config["host"], "Execution receipt names another application")
        require(receipt.get("value") == 4950 and receipt.get("initialPID") != receipt.get("replacementPID"),
                "Python computation or respawn failed")
        contained_runtime(receipt.get("pythonPrefix"), app)
        if probes:
            verify_source_denials(receipt.get("sourceReadDenial"), probes)
        log = (output / "console.log").read_text()
        acquired = re.findall(r"SWIFTPYTHON_EXTENSION_ACQUIRED worker=(\d+) pid=(\d+) incarnation=([A-F0-9-]+)", log)
        exits = set(re.findall(r"SWIFTPYTHON_KERNEL_PROCESS_EXIT pid=(\d+) identityValidated=true", log))
        retired = set(re.findall(r"SWIFTPYTHON_EXTENSION_RETIRE_CONFIRMED .* incarnation=([A-F0-9-]+) authority=kernel_process_exit", log))
        require(len(acquired) == 2 and {pid for _, pid, _ in acquired} ==
                {str(receipt["initialPID"]), str(receipt["replacementPID"])}, "Unexpected worker acquisitions")
        directory = "/Contents/XPCServices/" if config["host"] == "xpc-service" else "/Contents/Extensions/"
        for worker, pid, incarnation in acquired:
            require(pid in exits and incarnation in retired, f"No kernel retirement proof for worker {worker}: {pid}/{incarnation}")
            require(directory in observer.seen.get(pid, ""), f"Exact worker executable was not independently sampled: {pid}")
        require(observer.seen.get(str(receipt.get("hostPID")), "").startswith(str(app) + "/Contents/MacOS/"),
                "Host executable was not independently sampled")
        require(not observer.sample() and not observer.survivors(), "Owned processes or descendants survived shutdown")
        require(preflight(root, args.receipt) == before, "Signed application changed during execution")
        result.update({"passed": True, "host": config["host"], "app": str(app),
                       "finishedAtUnixSeconds": time.time(),
                       "osVersion": command("sw_vers", "-productVersion").strip(),
                       "osBuild": command("sw_vers", "-buildVersion").strip(),
                       "architecture": platform.machine(), "acquired": acquired,
                       "kernelExits": sorted(exits), "retired": sorted(retired),
                       "sealUnchanged": True,
                       "sourceAccessIsolation": "sandbox-read-denial-proven" if probes else "not-tested",
                       "sourceReadDenial": receipt.get("sourceReadDenial"),
                       "foreignTeam": "not-tested", "releaseCorpus": "not-run", "notarization": "not-run",
                       "consumerReceiptSHA256": sha256(output / "receipt.json")})
        return result
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        observer.sample()
        survivors = observer.survivors()
        if survivors:
            observer.cleanup()
        write_json(output / "processes.json", {"startedAtUnixSeconds": started, "seen": observer.seen, "owned": observer.owned,
                   "samples": samples, "survivorsBeforeCleanup": survivors, "survivors": observer.survivors()})
        if launch.poll() is None:
            launch.terminate()
            launch.wait(timeout=5)
        write_json(output / "verification.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--deny-source", type=Path, action="append", default=[],
                        help="Existing private source file the sandboxed parent and workers must be denied reading; repeatable")
    try:
        print(json.dumps(verify(parser.parse_args()), sort_keys=True))
    except (ContractError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"stage": "execution", "passed": False, "error": str(error)}), file=sys.stderr)
        sys.exit(1)
