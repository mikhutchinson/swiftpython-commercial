#!/usr/bin/env python3
"""SwiftPython VM Worker — Pure Python implementation of the MessageFrame IPC protocol.

Speaks the same wire protocol as the compiled SwiftPythonWorker binary, but runs
natively inside a macOS guest VM. Communicates with the host via AF_VSOCK.

Wire format (v6 — v5 framing plus live capability and duplex control cases):
    ┌──────────────┬──────────────┬──────────────┬─────────────────┬──────────────────┐
    │ JSONLen (4B) │ BinLen (4B)  │ Type (1B)    │ JSON Payload    │ Binary Payload   │
    │ UInt32 LE    │ UInt32 LE    │ 0=Cmd 1=Resp │ Variable length │ Variable length  │
    └──────────────┴──────────────┴──────────────┴─────────────────┴──────────────────┘

Binary sidecar for RemoteValueDescriptors:
    [UInt32 entryCount][UInt32 len0][UInt32 len1]...[bytes0][bytes1]...
"""

import ast
import base64
import collections
import importlib
import json
import os
import pickle
import queue
import select
import signal
import socket
import struct
import sys
import textwrap
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

# The wire vocabulary is generated from the Swift types that own it. Python
# already puts the script's directory on `sys.path`; resolve it explicitly so a
# renamed or symlinked install (the image builders install this as
# `/usr/local/bin/swiftpython-worker`) still finds the module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _swiftpython_wire import (  # noqa: E402
    COMMAND_CASES,
    CURRENT_PROTOCOL_VERSION,
    RESPONSE_CASES,
    SESSION_ROUTED_RESPONSES,
    STREAM_CHANNEL_RESPONSES,
    SCHEDULING_GENERAL_COMMANDS,
    SCHEDULING_STREAM_COMMANDS,
    SCHEDULING_DUPLEX_COMMANDS,
    WORKER_SCHEDULING_LIMITS,
    FRAME_WRITE_LIMITS,
)
import _swiftpython_duplex as _duplex_helper  # noqa: E402
from swiftpython_frames import (  # noqa: E402
    FrameValidationError,
    HEADER_SIZE,
    HOST_CID,
    MAX_PAYLOAD_BYTES,
    MSG_TYPE_COMMAND,
    MSG_TYPE_RESPONSE,
    MSG_TYPE_SIDE,
    accept_vsock_listener,
    bind_vsock_listener,
    connect_uds,
    connect_vsock,
    encode_frame,
    inject_binary_into_command,
    recv_exact,
    receive_frame,
    send_all,
    send_all_shared,
    worker_capability_declaration,
    decode_command_envelope,
)
from swiftpython_guest_duplex import (  # noqa: E402
    _GuestDuplexCleanupError,
    _GuestDuplexAcceleratorResourceError,
    _GuestDuplexError,
    _GuestDuplexResourceError,
    _GuestDuplexSessionManager,
)

# ---------------------------------------------------------------------------
# Socket I/O helpers
# ---------------------------------------------------------------------------


class CallbackDependencyExecutor:
    """Immediate bounded dependency grants, held through exact terminal write."""

    def __init__(self, worker):
        self.worker = worker
        self.condition = threading.Condition()
        self.jobs = []
        self.threads = []
        self.idle = 0
        self.closed = False
        self.active = 0
        self.retained_bytes = 0

    def submit(self, job, *, depth, input_bytes):
        charged = input_bytes + 1024
        with self.condition:
            if (self.closed or not 0 < depth <= WORKER_SCHEDULING_LIMITS["maximumDependencyDepth"]
                    or input_bytes < 0 or self.active >= WORKER_SCHEDULING_LIMITS["maximumDependencies"]
                    or charged > WORKER_SCHEDULING_LIMITS["maximumDependencyBytes"] - self.retained_bytes):
                raise _GuestDuplexResourceError("dependency count, depth or input bytes exhausted")
            self.active += 1
            self.retained_bytes += charged
            item = (job, charged)
            self.jobs.append(item)
            if len(self.jobs) > self.idle and len(self.threads) < WORKER_SCHEDULING_LIMITS["maximumDependencies"]:
                thread = threading.Thread(target=self._run, name="swiftpython-vm-owned-reentry", daemon=True)
                try:
                    thread.start()
                except BaseException:
                    self.jobs.remove(item)
                    self.active -= 1
                    self.retained_bytes -= charged
                    raise
                self.threads.append(thread)
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                while not self.jobs and not self.closed:
                    self.idle += 1
                    try:
                        self.condition.wait()
                    finally:
                        self.idle -= 1
                if not self.jobs:
                    return
                job, charged = self.jobs.pop(0)
            try:
                job()
            except BaseException:
                # An uncertain terminal write never frees admission. The exact
                # endpoint is retired rather than accepting more user work.
                self.worker._retire_failed_endpoint()
                return
            with self.condition:
                self.active -= 1
                self.retained_bytes -= charged

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def join(self, timeout):
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
        return all(not thread.is_alive() for thread in self.threads)


def retained_command_bytes(value, wire_bytes=0, maximum=16 * 1024 * 1024 + 1):
    """Charge serialized storage plus decoded built-in container capacity."""
    seen = set()
    stack = [value]
    total = wire_bytes
    while stack and total < maximum:
        item = stack.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        if type(item) is dict:
            total += sys.getsizeof(item)
            stack.extend(item.keys())
            stack.extend(item.values())
        elif type(item) in (list, tuple):
            total += sys.getsizeof(item)
            stack.extend(item)
        elif type(item) not in (str, bytes):
            total += sys.getsizeof(item)
    return min(maximum, total)


class FrameWriteGate:
    """Bounded atomic frames: eight progress opportunities, then ordinary."""
    def __init__(self):
        self.cv = threading.Condition()
        self.groups = collections.OrderedDict()
        self.counts = [0, 0]
        self.bytes = [0, 0]
        self.progress_owners = {}
        self.held = False
        self.closed = False
        self.in_progress_bytes = 0
        self.turn = 0

    def _remove(self, job):
        owner = job["owner"]
        self.groups[owner].remove(job)
        if not self.groups[owner]:
            del self.groups[owner]
        index = int(job["progress"])
        self.counts[index] -= 1
        self.bytes[index] -= job["bytes"]
        if index:
            self.progress_owners[owner] -= 1
            if self.progress_owners[owner] == 0:
                del self.progress_owners[owner]

    def _grant(self):
        if self.held or self.closed:
            return
        selected = None
        for wanted in (self.turn < FRAME_WRITE_LIMITS["progressBurst"], self.turn >= FRAME_WRITE_LIMITS["progressBurst"]):
            for owner, jobs in self.groups.items():
                if jobs[0]["progress"] == wanted:
                    selected = jobs[0]
                    break
            if selected is not None:
                break
        if selected is None:
            return
        self._remove(selected)
        if selected["owner"] in self.groups:
            self.groups.move_to_end(selected["owner"])
        selected["granted"] = True
        self.held = True
        self.in_progress_bytes = selected["bytes"]
        self.turn = (self.turn + 1) % (FRAME_WRITE_LIMITS["progressBurst"] + 1)
        self.cv.notify_all()

    def acquire(self, owner, size, progress, deadline):
        with self.cv:
            if self.closed:
                raise ConnectionError("writer endpoint retired")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("writer deadline expired before admission")
            if not self.held and not self.groups:
                self.held = True
                self.in_progress_bytes = size
                return
            progress = progress and size <= FRAME_WRITE_LIMITS["progressFrameBytes"]
            index = int(progress)
            max_count = FRAME_WRITE_LIMITS["progressCount" if progress else "ordinaryCount"]
            max_bytes = FRAME_WRITE_LIMITS["progressBytes" if progress else "ordinaryBytes"]
            if (self.counts[index] >= max_count or size > max_bytes - self.bytes[index]
                    or (progress and self.progress_owners.get(owner, 0) >= FRAME_WRITE_LIMITS["progressPerOwner"])):
                raise _GuestDuplexResourceError("writer pending count or bytes exhausted")
            job = dict(id=str(uuid.uuid4()), owner=owner, bytes=size, progress=progress, granted=False)
            self.groups.setdefault(owner, []).append(job)
            self.counts[index] += 1
            self.bytes[index] += size
            if progress:
                self.progress_owners[owner] = self.progress_owners.get(owner, 0) + 1
            if not self.held:
                self._grant()
            while not job["granted"]:
                if self.closed or (deadline is not None and time.monotonic() >= deadline):
                    self._remove(job)
                    self._grant()
                    if self.closed:
                        raise ConnectionError("writer endpoint retired")
                    raise TimeoutError("writer deadline expired while queued")
                self.cv.wait(None if deadline is None else max(0, deadline - time.monotonic()))
            if self.closed or (deadline is not None and time.monotonic() >= deadline):
                self.held = False
                self.in_progress_bytes = 0
                self._grant()
                raise TimeoutError("writer grant expired before frame commitment")

    def release(self):
        with self.cv:
            self.held = False
            self.in_progress_bytes = 0
            self._grant()
            self.cv.notify_all()

    @contextmanager
    def frame(self, owner, size, progress, deadline):
        self.acquire(owner, size, progress, deadline)
        try:
            yield
        finally:
            self.release()

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()


class ControlPayloadStore:
    """Opaque peer descriptors remain charged through application or cleanup."""
    def __init__(self, worker):
        self.worker = worker
        self.lock = threading.RLock()
        self.entries = {}
        self.owners = {}
        self.bytes = 0

    def record(self, descriptor, session_id):
        payload_bytes = 0
        if descriptor is not None:
            if "pickle" in descriptor:
                blob = descriptor["pickle"].get("_0", b"")
                payload_bytes = len(blob.encode("utf-8")) if isinstance(blob, str) else len(blob)
            else:
                payload_bytes = len(json.dumps(descriptor).encode("utf-8"))
        charged = 256 + payload_bytes + retained_command_bytes(descriptor, maximum=1024 * 1024 + 1)
        with self.lock:
            if (len(self.entries) >= 64 or self.owners.get(session_id, 0) >= 16
                    or charged > 1024 * 1024 - self.bytes):
                raise _GuestDuplexResourceError("control descriptor storage exhausted")
            pinned = None
            if descriptor is not None and "handle" in descriptor:
                pinned = descriptor["handle"].get("id", "")
                with self.worker.object_store_lock:
                    if pinned not in self.worker.object_store or pinned in self.worker.deferred_object_releases:
                        raise HandleNotFoundError("Control handle is not live")
                    self.worker.control_object_pins[pinned] = self.worker.control_object_pins.get(pinned, 0) + 1
            token = str(uuid.uuid4())
            self.entries[token] = (session_id, descriptor, charged, pinned)
            self.owners[session_id] = self.owners.get(session_id, 0) + 1
            self.bytes += charged
            return token

    def resolve(self, token):
        with self.lock:
            entry = self.entries[token]
        return None if entry[1] is None else self.worker._resolve_value_descriptor(entry[1])

    def settle(self, token):
        with self.lock:
            entry = self.entries.pop(token, None)
            if entry is None:
                return
            owner, _, charged, pinned = entry
            self.bytes -= charged
            self.owners[owner] -= 1
            if self.owners[owner] == 0:
                del self.owners[owner]
        if pinned is not None:
            with self.worker.object_store_lock:
                count = self.worker.control_object_pins[pinned] - 1
                if count:
                    self.worker.control_object_pins[pinned] = count
                else:
                    del self.worker.control_object_pins[pinned]
                    if pinned in self.worker.deferred_object_releases:
                        self.worker.deferred_object_releases.remove(pinned)
                        self.worker.object_store.pop(pinned, None)

    def settle_session(self, session_id):
        with self.lock:
            tokens = [token for token, entry in self.entries.items() if entry[0] == session_id]
        for token in tokens:
            self.settle(token)


class WorkerExecutionAdmission:
    """One peer registry owns all six lanes and bounded fair pending work."""

    def __init__(self, worker):
        self.worker = worker
        self.lock = threading.RLock()
        self.closed = False
        self.active = {}
        self.groups = collections.OrderedDict()
        self.active_groups = set()
        self.pending_bytes = 0
        self.pending_count = 0
        self.turn = 0
        self.finished = collections.OrderedDict()
        self.streams = {}
        self.closed_streams = collections.OrderedDict()
        self.duplex = {}
        self.control_count = 0
        self.control_bytes = 0
        self.control_owners = {}
        self.cleanup_count = 0
        self.cleanup_bytes = 0
        self.limits = {key: WORKER_SCHEDULING_LIMITS[key] for key in
                       ("general", "io", "computeStreams", "ioStreams", "computeDuplex", "ioDuplex", "side")}
        self.used = {key: 0 for key in self.limits}
        self.executors = {key: ThreadPoolExecutor(max_workers=count, thread_name_prefix="swiftpython-vm-" + key)
                          for key, count in self.limits.items()}
        self.control_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="swiftpython-vm-management")
        self.cleanup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="swiftpython-vm-cleanup")

    @staticmethod
    def family(name):
        if name in SCHEDULING_GENERAL_COMMANDS:
            return "general"
        if name in SCHEDULING_STREAM_COMMANDS:
            return "stream"
        if name in SCHEDULING_DUPLEX_COMMANDS:
            return "duplex"
        return None

    def _can_grant(self, job):
        return job["group"] not in self.active_groups and self.used[job["lane"]] < self.limits[job["lane"]]

    def _grant(self, job):
        assert self._can_grant(job)
        self.active[job["id"]] = job
        self.active_groups.add(job["group"])
        self.used[job["lane"]] += 1
        return job

    def _remove_pending(self, job):
        group = self.groups[job["group"]]
        group["jobs"].remove(job)
        group["bytes"] -= job["bytes"]
        self.pending_count -= 1
        self.pending_bytes -= job["bytes"]
        if not group["jobs"]:
            del self.groups[job["group"]]

    def _next(self):
        for foreground in (self.turn < 4, self.turn >= 4):
            for group_id, group in list(self.groups.items()):
                job = group["jobs"][0]
                if (job["priority"] == "foreground") != foreground or not self._can_grant(job):
                    continue
                self._remove_pending(job)
                if group_id in self.groups:
                    self.groups.move_to_end(group_id)
                self.turn = (self.turn + 1) % 5
                return self._grant(job)
        return None

    def _ready(self):
        jobs = []
        while not self.closed and self.worker.running:
            job = self._next()
            if job is None:
                break
            jobs.append(job)
        return jobs

    def _dispatch(self, jobs):
        for job in jobs:
            try:
                self.executors[job["lane"]].submit(self._execute, job)
            except BaseException:
                self.worker._retire_failed_endpoint()
                raise

    def submit(self, name, data, metadata, input_bytes, side=False):
        family = ("stream" if data.get("generator") else "side") if side else self.family(name)
        if family is None:
            raise ValueError("Progress command has no ordinary execution family")
        if metadata is None:
            identity = str(uuid.uuid4()).upper()
            metadata = {"requestID": identity, "groupID": identity,
                        "priority": "background", "cost": "unspecified"}
        io = metadata["cost"] == "ioBound"
        lane = "side" if family == "side" else ("io" if io else "general") if family == "general" else (
            ("ioStreams" if io else "computeStreams") if family == "stream" else
            ("ioDuplex" if io else "computeDuplex"))
        channel = self.worker._command_channel_id(name, data)
        job = {"id": metadata["requestID"], "group": metadata["groupID"], "priority": metadata["priority"],
               "lane": lane, "family": family, "name": name, "data": data,
               "bytes": input_bytes + 256, "channel": channel, "transferred": False}
        grants = []
        try:
            with self.lock:
                if self.closed or not self.worker.running:
                    raise _GuestDuplexResourceError("execution admission closed")
                if job["id"] in self.active or job["id"] in self.finished or any(
                        item["id"] == job["id"] for group in self.groups.values() for item in group["jobs"]):
                    raise _GuestDuplexResourceError("duplicateRequest")
                if family == "stream" and not side and (channel in self.streams or channel in self.closed_streams):
                    self.worker._retire_failed_endpoint()
                    raise ConnectionError("reused stream route")
                grants = self._ready()
                has_eligible = any(self._can_grant(group["jobs"][0]) for group in self.groups.values())
                if job["group"] not in self.groups and not has_eligible and self._can_grant(job):
                    grants.append(self._grant(job))
                else:
                    group = self.groups.get(job["group"], {"jobs": collections.deque(), "bytes": 0})
                    if (self.pending_count >= WORKER_SCHEDULING_LIMITS["maximumPendingRequests"]
                            or job["bytes"] > WORKER_SCHEDULING_LIMITS["maximumPendingBytes"] - self.pending_bytes
                            or len(group["jobs"]) >= WORKER_SCHEDULING_LIMITS["maximumPendingRequestsPerGroup"]
                            or job["bytes"] > WORKER_SCHEDULING_LIMITS["maximumPendingBytesPerGroup"] - group["bytes"]):
                        raise _GuestDuplexResourceError("queueFull: execution queue count or input bytes exhausted")
                    self.groups[job["group"]] = group
                    group["jobs"].append(job)
                    group["bytes"] += job["bytes"]
                    self.pending_count += 1
                    self.pending_bytes += job["bytes"]
                if family == "stream" and not side:
                    self.streams[channel] = job
                    self.worker.stream_credit.reserve(channel)
        finally:
            self._dispatch(grants)

    def _execute(self, job):
        previous = getattr(self.worker.active_command_channel, "admission_grant", None)
        self.worker.active_command_channel.admission_grant = job
        try:
            if job["name"] == "trackedEval":
                previous_channel = getattr(self.worker.active_command_channel, "channel_id", None)
                self.worker.active_command_channel.channel_id = job["channel"]
                try:
                    code = job["data"]["code"]
                    exec(compile(code, "<tracked-side>", "exec"), self.worker.namespace, self.worker.namespace)
                except Exception as error:
                    print(f"[worker {self.worker.worker_id}] tracked side error: {error}", file=sys.stderr, flush=True)
                finally:
                    self.worker.side_quiescence.end_tracked()
                    self.worker.active_command_channel.channel_id = previous_channel
                self.worker._send_response("sideExecutionFinished", {
                    "requestID": job["id"], "generation": int(job["data"]["expectedGeneration"])
                }, b"", channel_id=job["channel"])
            elif job["family"] == "stream":
                self.worker._execute_stream(job["name"], job["data"])
            else:
                self.worker._handle_and_send(job["name"], job["data"])
        except BaseException:
            self.worker._retire_failed_endpoint()
            return
        finally:
            self.worker.active_command_channel.admission_grant = previous
        if not job["transferred"]:
            self.complete(job)

    def _remember(self, table, key):
        table[key] = None
        table.move_to_end(key)
        while len(table) > 4096:
            table.popitem(last=False)

    def complete(self, job):
        with self.lock:
            if self.active.get(job["id"]) is not job:
                return
            del self.active[job["id"]]
            self.active_groups.remove(job["group"])
            self.used[job["lane"]] -= 1
            self._remember(self.finished, job["id"])
            if job["family"] == "stream":
                self.streams.pop(job["channel"], None)
                self._remember(self.closed_streams, job["channel"])
            grants = self._ready()
        self._dispatch(grants)

    def bind_duplex(self, session):
        job = getattr(self.worker.active_command_channel, "admission_grant", None)
        if job is None:
            return
        with self.lock:
            if job["family"] != "duplex" or self.active.get(job["id"]) is not job:
                raise RuntimeError("duplex grant ownership mismatch")
            key = (session.session_id, session.token)
            self.duplex[key] = job
            job["transferred"] = True

    def complete_duplex(self, session):
        with self.lock:
            job = self.duplex.pop((session.session_id, session.token), None)
        if job is not None:
            self.complete(job)

    def cancel_stream(self, channel):
        with self.lock:
            job = self.streams.get(channel)
            if job is None:
                return
            if job["id"] in self.active:
                self.worker._signal_stream_cancel(channel)
                return
            self._remove_pending(job)
            # The route stays owned through the exact zero-execution terminal.
        self.worker.stream_credit.end(channel)
        self.worker._send_response("streamEnd", {}, b"", channel_id=channel)
        with self.lock:
            self.streams.pop(channel, None)
            self._remember(self.finished, job["id"])
            self._remember(self.closed_streams, channel)
            grants = self._ready()
        self._dispatch(grants)

    def submit_control(self, name, data, input_bytes):
        charged = input_bytes + 256
        owner = data.get("sessionID", name)
        with self.lock:
            if (self.closed or self.control_count >= 64 or charged > 1024 * 1024 - self.control_bytes
                    or self.control_owners.get(owner, 0) >= 16):
                raise _GuestDuplexResourceError("management queue count or input bytes exhausted")
            self.control_count += 1
            self.control_bytes += charged
            self.control_owners[owner] = self.control_owners.get(owner, 0) + 1
        def execute():
            try:
                if name == "duplexClose":
                    channel = self.worker._command_channel_id(name, data)
                    try:
                        barrier = self.worker.duplex_sessions.record_close(
                            data["sessionID"], int(data["controlChannelID"]), int(data["controlSequence"]))
                        if barrier is None:
                            self.worker._send_response("success", {}, b"", channel_id=channel)
                        else:
                            self._submit_cleanup(barrier, channel)
                    except Exception as error:
                        self.worker._send_response("error", {"code": "resourceError", "message": str(error)},
                                                   b"", channel_id=channel)
                else:
                    self.worker._handle_and_send(name, data)
            except BaseException:
                self.worker._retire_failed_endpoint()
                return
            finally:
                with self.lock:
                    self.control_count -= 1
                    self.control_bytes -= charged
                    self.control_owners[owner] -= 1
                    if self.control_owners[owner] == 0:
                        del self.control_owners[owner]
        self.control_executor.submit(execute)

    def _submit_cleanup(self, barrier, channel):
        charged = 512
        with self.lock:
            if self.closed or self.cleanup_count >= 64 or charged > 1024 * 1024 - self.cleanup_bytes:
                raise _GuestDuplexResourceError("cleanup queue exhausted")
            self.cleanup_count += 1
            self.cleanup_bytes += charged
        def execute():
            try:
                barrier()
                self.worker._send_response("success", {}, b"", channel_id=channel)
            except _GuestDuplexCleanupError as error:
                try:
                    self.worker._send_response("error", {"code": "executionError", "message": str(error)},
                                               b"", channel_id=channel)
                finally:
                    self.worker._retire_failed_endpoint()
            except BaseException:
                self.worker._retire_failed_endpoint()
            finally:
                with self.lock:
                    self.cleanup_count -= 1
                    self.cleanup_bytes -= charged
        self.cleanup_executor.submit(execute)

    def close(self):
        with self.lock:
            self.closed = True
            pending = [job for group in self.groups.values() for job in group["jobs"]]
            self.groups.clear()
            self.pending_bytes = self.pending_count = 0
        for job in pending:
            if job["family"] == "stream":
                self.worker.stream_credit.end(job["channel"])
        for executor in self.executors.values():
            executor.shutdown(wait=False)
        self.control_executor.shutdown(wait=False)
        self.cleanup_executor.shutdown(wait=False)


class StreamCreditLedger:
    """Credit for ``stream.credit.v1`` paced streams.

    A grant that arrives before its stream starts waits in a bounded pending
    table (the host writes the opening grant just before the stream command).
    ``begin`` moves it to the stream, which is then paced: every streamChunk
    and streamProgress it sends first ``acquire``s one credit, blocking while
    none is left. A stream with no opening grant is unpaced.
    """

    PENDING_LIMIT = 64
    STOP_POLL_SECONDS = 0.05

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: "collections.OrderedDict[int, int]" = collections.OrderedDict()
        self._active: dict[int, int] = {}
        self._reserved: dict[int, int | None] = {}

    def reserve(self, channel_id: int) -> None:
        with self._condition:
            if channel_id in self._reserved or channel_id in self._active:
                raise ValueError("stream credit route already reserved")
            self._reserved[channel_id] = self._pending.pop(channel_id, None)

    def grant(self, channel_id: int, credits: int) -> None:
        if credits <= 0:
            return
        with self._condition:
            if channel_id in self._active:
                self._active[channel_id] += credits
                self._condition.notify_all()
                return
            if channel_id in self._reserved:
                self._reserved[channel_id] = (self._reserved[channel_id] or 0) + credits
                return
            self._pending[channel_id] = self._pending.get(channel_id, 0) + credits
            while len(self._pending) > self.PENDING_LIMIT:
                self._pending.popitem(last=False)

    def begin(self, channel_id: int) -> bool:
        with self._condition:
            credits = self._reserved.pop(channel_id, self._pending.pop(channel_id, None))
            if credits is None:
                return False
            self._active[channel_id] = credits
            return True

    def acquire(self, channel_id: int, should_stop) -> bool:
        with self._condition:
            while self._active.get(channel_id) == 0:
                if should_stop():
                    return False
                self._condition.wait(self.STOP_POLL_SECONDS)
            if channel_id in self._active:
                self._active[channel_id] -= 1
            return True

    def end(self, channel_id: int) -> None:
        with self._condition:
            self._active.pop(channel_id, None)
            self._reserved.pop(channel_id, None)
            self._pending.pop(channel_id, None)
            self._condition.notify_all()

    def wake_all(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def is_paced(self, channel_id: int) -> bool:
        with self._condition:
            return channel_id in self._active


class HandleNotFoundError(KeyError):
    """A command named a handle this worker does not hold."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "Handle not found"


# ---------------------------------------------------------------------------
# Streaming callback iterator
# ---------------------------------------------------------------------------


class _SwiftStreamIterator:
    """Iterator returned by ``swift_bridge.call_stream(name, *args)``.

    Each ``__next__()`` call sends a ``callbackStreamNext`` response to the
    Swift host, which pulls the next chunk from the Swift-side iterator and
    sends it back as ``callbackStreamChunk``, ``callbackStreamEnd``, or
    ``callbackStreamError``.
    """

    def __init__(self, worker: "Worker", call_id: int):
        self._worker = worker
        self._call_id = call_id
        self._exhausted = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._exhausted:
            raise StopIteration
        try:
            return self._worker._execute_stream_next_via_ipc(self._call_id)
        except StopIteration:
            self._exhausted = True
            raise


# ---------------------------------------------------------------------------
# Worker implementation
# ---------------------------------------------------------------------------


SIDE_ORDERING_BOUND_SECONDS = 1.0

# Main-channel commands that run user code and therefore observe side effects.
SIDE_ORDERED_COMMANDS = frozenset({
    "eval", "evalResult", "invoke", "invokeResult", "method", "methodResult", "store",
    "attachSharedMemory", "copyToShared", "duplexOpen",
    "methodStream", "invokeStream", "evalStream",
})


class SideChannelQuiescence:
    """Orders delivered side commands before later main-channel user commands.

    A host ``sideEval`` returns once its frame is written, so its bytes are
    already in this worker's side socket when a later main command arrives.
    Waiting until the side loop is idle with an empty socket therefore makes
    those effects visible. Bounded per frame: a side frame running longer than
    ``SIDE_ORDERING_BOUND_SECONDS`` stays concurrent and is never waited for
    again, so side work can never stall main work. Mirrors the Swift worker's
    ``SideChannelQuiescence``.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._tracked = 0
        self._busy = False
        self._running = False
        self._frame_started = 0.0

    def mark_running(self):
        with self._condition:
            self._running = True
            self._busy = False

    def mark_busy(self):
        with self._condition:
            self._busy = True
            self._frame_started = time.monotonic()

    def begin_tracked(self):
        with self._condition:
            self._tracked += 1
            self._busy = True
            self._frame_started = time.monotonic()

    def end_tracked(self):
        with self._condition:
            self._tracked -= 1
            if self._tracked == 0:
                self._busy = False
            self._condition.notify_all()

    def mark_idle(self):
        with self._condition:
            if self._tracked == 0:
                self._busy = False
            self._condition.notify_all()

    def mark_stopped(self):
        with self._condition:
            self._running = False
            self._busy = False
            self._condition.notify_all()

    @staticmethod
    def _has_unread_bytes(sock) -> bool:
        try:
            readable, _, _ = select.select([sock], [], [], 0)
        except (OSError, ValueError):
            return False
        return bool(readable)

    def wait_for_delivered_commands(self, sock):
        if sock is None:
            return
        deadline = time.monotonic() + SIDE_ORDERING_BOUND_SECONDS
        with self._condition:
            while self._running:
                now = time.monotonic()
                if now >= deadline:
                    return
                if self._busy and now - self._frame_started >= SIDE_ORDERING_BOUND_SECONDS:
                    return
                if not (self._busy or self._has_unread_bytes(sock)):
                    return
                # Bytes can arrive without a notification; a short slice rechecks.
                self._condition.wait(min(0.002, deadline - now))


class Worker:
    def __init__(
        self,
        sock: socket.socket,
        worker_id: int,
        ipc_config: dict,
        transport_mode: str,
    ):
        self.sock = sock
        self.worker_id = worker_id
        self.ipc_config = ipc_config
        self.max_payload = ipc_config.get("maxPayloadBytes", MAX_PAYLOAD_BYTES)
        self.running = True

        # Persistent namespace across evals (like a REPL)
        self.namespace = {"__builtins__": __builtins__}

        # Object store: UUID string -> Python object
        self.object_store: dict[str, object] = {}
        self.object_store_lock = threading.RLock()
        self.control_object_pins = {}
        self.deferred_object_releases = set()
        self.control_payloads = ControlPayloadStore(self)

        # Abort flag for cooperative stream cancellation
        self.abort_requested = False
        self.streaming_active = False
        self.cancel_flags: dict[int, threading.Event] = {}
        self.cancel_flags_lock = threading.Lock()
        self.stream_credit = StreamCreditLedger()
        self.active_command_channel = threading.local()
        self.active_stream_channel = threading.local()

        # Side channel
        self.side_sock: socket.socket | None = None
        self.side_quiescence = SideChannelQuiescence()
        self.side_thread: threading.Thread | None = None
        self.side_stopping = False

        # Callbacks (bidirectional IPC with Swift host)
        self._registered_callbacks: set[str] = set()
        self._swift_bridge_installed = False
        self._next_call_id: int = 1
        self._next_call_id_lock = threading.Lock()
        self._callback_waiters: dict[
            int,
            tuple["queue.Queue[tuple[str, dict, bytes]]", int, int],
        ] = {}
        self._callback_waiters_lock = threading.Lock()
        self._async_callback_waiters: dict[int, tuple["queue.Queue[tuple[str, dict, bytes]]", int, int]] = {}
        self._async_callback_waiters_lock = threading.Lock()
        self._active_stream_iterators: dict[int, object] = {}
        self._active_stream_iterators_lock = threading.Lock()
        self.response_writer = FrameWriteGate()
        self.duplex_sessions = _GuestDuplexSessionManager(
            self,
            transport_mode,
        )
        _duplex_helper._native = self.duplex_sessions.native_bridge
        sys.modules["swift_duplex"] = _duplex_helper
        self.namespace["swift_duplex"] = _duplex_helper

    def install_signal_handler(self):
        """Install SIGUSR1 handler for cooperative stream abort."""
        def _handler(signum, frame):
            if self.streaming_active:
                self.abort_requested = True
        signal.signal(signal.SIGUSR1, _handler)
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    def start_side_channel(self, side_sock: socket.socket):
        """Start the side channel daemon thread.

        The side channel receives fire-and-forget commands (sideEval) on a
        separate socket so they can execute while the main IPC socket is held
        by a streaming command. Commands are MessageFrame-encoded with type=2.
        """
        self.side_sock = side_sock
        self.side_quiescence.mark_running()
        self.side_thread = threading.Thread(
            target=self._side_channel_loop, daemon=True
        )
        self.side_thread.start()

    def _side_channel_loop(self):
        """Read side channel commands until the socket closes."""
        sock = self.side_sock
        quiescence = self.side_quiescence
        try:
            while not self.side_stopping:
                # Idle only at a frame boundary; bytes arriving while idle stay
                # visible as unread until this loop marks itself busy.
                quiescence.mark_idle()
                while not self.side_stopping:
                    readable, _, _ = select.select([sock], [], [], 0.25)
                    if readable:
                        break
                if self.side_stopping:
                    break
                quiescence.mark_busy()
                try:
                    msg_type, json_payload, _ = receive_frame(sock, self.max_payload)
                except ConnectionError:
                    break
                if msg_type != MSG_TYPE_SIDE and msg_type != MSG_TYPE_COMMAND:
                    # Unknown types are skipped only after the shared bound.
                    continue
                cmd = json.loads(json_payload)
                cmd_name = next(iter(cmd))
                cmd_data = cmd[cmd_name]
                quiescence.mark_busy()
                self._dispatch_side_command(cmd_name, cmd_data)
        except Exception as e:
            if not self.side_stopping:
                print(f"[worker {self.worker_id}] side channel error: {e}", file=sys.stderr, flush=True)
        finally:
            quiescence.mark_stopped()
            try:
                sock.close()
            except Exception:
                pass

    def _dispatch_side_command(self, cmd_name: str, cmd_data: dict):
        """Tracked work uses the same bounded peer lane registry as main work."""
        if cmd_name == "trackedEval":
            metadata = cmd_data["scheduling"]
            request = str(uuid.UUID(cmd_data["requestID"])).upper()
            if (str(uuid.UUID(metadata["requestID"])).upper() != request
                    or metadata["priority"] not in ("foreground", "background")
                    or metadata["cost"] not in ("unspecified", "cpuBound", "ioBound")
                    or len(cmd_data["code"].encode("utf-8")) > 65536):
                raise ValueError("invalid tracked side envelope")
            metadata = dict(metadata, requestID=request, groupID=str(uuid.UUID(metadata["groupID"])).upper())
            channel = int(cmd_data["channelID"])
            self.side_quiescence.begin_tracked()
            try:
                # Acknowledgement precedes any completion on this exact route.
                self._send_response("success", {}, b"", channel_id=channel)
                self.execution_admission.submit(cmd_name, cmd_data, metadata,
                    len(cmd_data["code"].encode("utf-8")), side=True)
            except Exception:
                self.side_quiescence.end_tracked()
                self._send_response("sideExecutionFinished", {"requestID": request,
                    "generation": int(cmd_data["expectedGeneration"])}, b"", channel_id=channel)
                raise
        elif cmd_name == "eval":
            code = cmd_data.get("code", "")
            try:
                exec(compile(code, "<side-eval>", "exec"), self.namespace, self.namespace)
            except Exception as e:
                print(f"[worker {self.worker_id}] sideEval error: {e}", file=sys.stderr, flush=True)
        elif cmd_name == "startOOBStream":
            self._start_oob_socket_stream(cmd_data)
        else:
            print(f"[worker {self.worker_id}] unknown side command: {cmd_name}", file=sys.stderr, flush=True)

    def _start_oob_socket_stream(self, cmd_data: dict):
        """Start an out-of-band socket stream on a daemon thread.

        The generator runs in a background thread. Each yielded value is
        length-prefixed and written to a socket connection to the host.
        Abort: host closes its end → Python gets EPIPE/BrokenPipeError.
        Done: Python closes its end → host gets EOF.

        cmd_data keys:
            generatorCode: Python expression evaluating to an iterable
            socketPath: UDS path to connect to (for process/test backend)
            vsockPort: vsock port to connect to (for VM backend)
            vsockCID: vsock CID (default HOST_CID=2)
        """
        generator_code = cmd_data.get("generatorCode", "")
        socket_path = cmd_data.get("socketPath")
        vsock_port = cmd_data.get("vsockPort")
        vsock_cid = cmd_data.get("vsockCID", HOST_CID)

        ns = self.namespace

        def _oob_socket_writer():
            oob_sock = None
            try:
                if vsock_port is not None:
                    # Listen on vsock port; host connects via device.connect(toPort:)
                    AF_VSOCK = 40
                    VSOCK_CID_ANY = -1
                    server = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    server.bind((VSOCK_CID_ANY, vsock_port))
                    server.listen(1)
                    oob_sock, _ = server.accept()
                    server.close()
                elif socket_path is not None:
                    oob_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    oob_sock.connect(socket_path)
                else:
                    print(f"[worker {self.worker_id}] OOB stream: no socket path or vsock port",
                          file=sys.stderr, flush=True)
                    return

                for chunk in eval(compile(generator_code, "<oob-gen>", "eval"), ns, ns):
                    if self.abort_requested:
                        break
                    if isinstance(chunk, str):
                        data = chunk.encode("utf-8")
                    elif isinstance(chunk, bytes):
                        data = chunk
                    else:
                        raise TypeError(
                            f"OOB stream: expected str or bytes, got {type(chunk).__name__}"
                        )
                    # Length-prefixed: [4-byte LE length][data]
                    send_all(oob_sock, struct.pack("<I", len(data)), data)
            except (BrokenPipeError, ConnectionError, ConnectionResetError):
                pass  # host closed → abort
            except Exception as e:
                print(f"[worker {self.worker_id}] OOB stream error: {e}",
                      file=sys.stderr, flush=True)
            finally:
                if oob_sock is not None:
                    try:
                        oob_sock.close()
                    except Exception:
                        pass

        threading.Thread(target=_oob_socket_writer, daemon=True).start()

    def run(self):
        self.install_signal_handler()
        self.execution_admission = WorkerExecutionAdmission(self)
        self.callback_dependencies = CallbackDependencyExecutor(self)
        while self.running:
            error_channel_id = None
            try:
                cmd_name, cmd_data, binary, scheduling, owner, input_bytes = self._receive_command()
                error_channel_id = self._command_channel_id(cmd_name, cmd_data)
                if owner is not None:
                    self._route_owned_callback_command(owner, cmd_name, cmd_data, binary, input_bytes)
                    continue
                cmd_data = inject_binary_into_command(cmd_name, cmd_data, binary)
                if self._route_callback_reply(cmd_name, cmd_data, binary):
                    continue
                if cmd_name == "streamCredit":
                    self.stream_credit.grant(int(cmd_data.get("streamChannelID", 0) or 0),
                                             int(cmd_data.get("grant", 0) or 0))
                elif cmd_name == "streamCancel":
                    self._handle_and_send(cmd_name, cmd_data)
                elif cmd_name == "shutdown":
                    self._handle_and_send(cmd_name, cmd_data)
                    break
                elif self.execution_admission.family(cmd_name) is not None:
                    self.execution_admission.submit(cmd_name, cmd_data, scheduling, input_bytes)
                else:
                    self.execution_admission.submit_control(cmd_name, cmd_data, input_bytes)
            except (ConnectionError, FrameValidationError):
                break
            except Exception as e:
                if self.sock.fileno() < 0 or not self.running:
                    break
                try:
                    self._send_response("error", {
                        "code": "resourceError" if isinstance(e, _GuestDuplexResourceError) else "unknown",
                        "message": f"Worker error: {e}",
                    }, b"", channel_id=error_channel_id)
                except Exception:
                    break
        self.running = False
        self.execution_admission.close()
        self.callback_dependencies.close()
        self.duplex_sessions.shutdown_all()
        self._fail_all_callback_waiters(ConnectionError("worker shutdown"))
        self._fail_all_async_callback_waiters(ConnectionError("worker shutdown"))
        if not self.callback_dependencies.join(2):
            raise RuntimeError("Owned callback commands did not quiesce")

    def _retire_failed_endpoint(self):
        self.response_writer.close()
        self.running = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _order_after_delivered_side_commands(self, cmd_name: str):
        if cmd_name in SIDE_ORDERED_COMMANDS and not getattr(self.active_command_channel, "owned_callback", False):
            self.side_quiescence.wait_for_delivered_commands(self.side_sock)

    def _handle_and_send(self, cmd_name: str, cmd_data: dict):
        self._order_after_delivered_side_commands(cmd_name)
        channel_id = self._command_channel_id(cmd_name, cmd_data)
        self.active_command_channel.channel_id = channel_id
        try:
            resp_name, resp_data, resp_binary = self._handle_command(cmd_name, cmd_data)
            sent_name = self._send_response(resp_name, resp_data, resp_binary, channel_id=channel_id)
            if cmd_name == "duplexOpen":
                session = self.duplex_sessions.session(cmd_data["sessionID"])
                if sent_name == "duplexOpened":
                    session.start()
                else:
                    # The open was refused on the actual wire. Reclaim its
                    # unpublished session without starting any handler or
                    # inventing a delivered opened/terminal receipt.
                    session.finish({"failed": {"code": "resourceError", "message": "Opened response exceeded frame ceiling"}},
                                   drain_writer=False, emit_terminal=False)
        except _GuestDuplexCleanupError as e:
            # An unquiesced session cannot yield a reusable worker. Wake the
            # main reader so main() retires this process and the host's exact
            # lifecycle owner observes EOF; never manufacture close success.
            try:
                self._send_response("error", {
                    "code": "executionError", "message": str(e),
                }, b"", channel_id=channel_id)
            finally:
                self.running = False
                self.sock.shutdown(socket.SHUT_RDWR)
        except _GuestDuplexAcceleratorResourceError as e:
            self._send_response("error", {
                "code": "acceleratorResourceError",
                "message": f"Worker error: {e}",
            }, b"", channel_id=channel_id)
        except _GuestDuplexResourceError as e:
            self._send_response("error", {
                "code": "resourceError",
                "message": f"Worker error: {e}",
            }, b"", channel_id=channel_id)
        except Exception as e:
            if os.environ.get("SWIFTPYTHON_IPC_LOG"):
                print(
                    f"[worker {self.worker_id}] {cmd_name} failed: {e}",
                    file=sys.stderr,
                    flush=True,
                )
            self._send_response("error", {
                "code": "executionError",
                "message": f"Worker error: {e}",
            }, b"", channel_id=channel_id)
        finally:
            if getattr(self.active_command_channel, "channel_id", None) == channel_id:
                self.active_command_channel.channel_id = 0

    def _command_channel_id(self, cmd_name: str, cmd_data: dict) -> int:
        if cmd_name in ("methodStream", "invokeStream", "evalStream"):
            return int(cmd_data.get("streamChannelID", 0) or 0)
        if cmd_name.startswith("duplex"):
            return int(cmd_data.get("controlChannelID", 0) or 0)
        return int(cmd_data.get("channelID", 0) or 0)

    def _current_callback_channel_id(self) -> int:
        if getattr(self.active_command_channel, "owned_callback", False):
            return int(self.active_command_channel.channel_id)
        stream_channel = int(getattr(self.active_stream_channel, "channel_id", 0) or 0)
        if stream_channel:
            return stream_channel
        return int(getattr(self.active_command_channel, "channel_id", 0) or 0)

    def _next_callback_call_id(self) -> int:
        with self._next_call_id_lock:
            call_id = self._next_call_id
            self._next_call_id += 1
            return call_id

    def _register_callback_waiter(
        self,
        call_id: int,
        channel_id: int,
    ) -> "queue.Queue[tuple[str, dict, bytes]]":
        waiter: "queue.Queue[tuple[str, dict, bytes]]" = queue.Queue()
        with self.duplex_sessions.callback_admission(
            getattr(self.active_command_channel, "duplex_token", None)
        ):
            with self._callback_waiters_lock:
                self._callback_waiters[call_id] = (waiter, channel_id, getattr(self.active_command_channel, "dependency_depth", 0))
        return waiter

    def _unregister_callback_waiter(self, call_id: int):
        with self._callback_waiters_lock:
            self._callback_waiters.pop(call_id, None)

    def _register_async_callback_waiter(self, call_id: int, channel_id: int) -> "queue.Queue[tuple[str, dict, bytes]]":
        waiter: "queue.Queue[tuple[str, dict, bytes]]" = queue.Queue()
        with self.duplex_sessions.callback_admission(
            getattr(self.active_command_channel, "duplex_token", None)
        ):
            with self._async_callback_waiters_lock:
                self._async_callback_waiters[call_id] = (waiter, channel_id, getattr(self.active_command_channel, "dependency_depth", 0))
        return waiter

    def _async_callback_waiter(self, call_id: int) -> tuple["queue.Queue[tuple[str, dict, bytes]]", int, int] | None:
        with self._async_callback_waiters_lock:
            return self._async_callback_waiters.get(call_id)

    def _unregister_async_callback_waiter(self, call_id: int):
        with self._async_callback_waiters_lock:
            self._async_callback_waiters.pop(call_id, None)

    def _route_owned_callback_command(self, call_id, cmd_name, cmd_data, binary, input_bytes):
        with self._callback_waiters_lock:
            entry = self._callback_waiters.get(call_id)
        if entry is None:
            with self._async_callback_waiters_lock:
                entry = self._async_callback_waiters.get(call_id)
        if entry is None:
            self._send_response("error", {
                "code": "internalError", "message": f"No active callback for nested command callId={call_id}",
            }, b"", channel_id=self._command_channel_id(cmd_name, cmd_data))
            return
        depth = entry[2] + 1
        def execute():
            with self._callback_waiters_lock:
                live = self._callback_waiters.get(call_id)
            if live is None:
                with self._async_callback_waiters_lock:
                    live = self._async_callback_waiters.get(call_id)
            if live is not entry or not self.running:
                self._send_response("error", {"code": "internalError", "message": "Callback owner retired"},
                                    b"", channel_id=self._command_channel_id(cmd_name, cmd_data))
                return
            arguments = inject_binary_into_command(cmd_name, cmd_data, binary)
            self._handle_owned_callback_command(cmd_name, arguments, depth)
        self.callback_dependencies.submit(execute, depth=depth, input_bytes=input_bytes)

    def _handle_owned_callback_command(self, cmd_name: str, cmd_data: dict, depth: int):
        parent = getattr(self.active_command_channel, "channel_id", 0)
        parent_owned = getattr(self.active_command_channel, "owned_callback", False)
        parent_depth = getattr(self.active_command_channel, "dependency_depth", 0)
        self.active_command_channel.owned_callback = True
        self.active_command_channel.dependency_depth = depth
        try:
            if cmd_name in SCHEDULING_STREAM_COMMANDS:
                self._execute_stream(cmd_name, cmd_data)
            else:
                self._handle_and_send(cmd_name, cmd_data)
        finally:
            self.active_command_channel.channel_id = parent
            self.active_command_channel.owned_callback = parent_owned
            self.active_command_channel.dependency_depth = parent_depth

    def _route_callback_reply(self, cmd_name: str, cmd_data: dict, binary: bytes) -> bool:
        if cmd_name not in (
            "callbackResult", "callbackError", "callbackStreamChunk",
            "callbackStreamEnd", "callbackStreamError",
        ):
            return False
        call_id = int(cmd_data.get("callId", -1))
        with self._async_callback_waiters_lock:
            async_waiter = self._async_callback_waiters.get(call_id)
        if async_waiter is not None:
            async_waiter[0].put((cmd_name, cmd_data, binary))
            return True

        with self._callback_waiters_lock:
            waiter_entry = self._callback_waiters.get(call_id)
        if waiter_entry is not None:
            waiter_entry[0].put((cmd_name, cmd_data, binary))
        return True

    def _fail_all_callback_waiters(self, error: Exception):
        with self._callback_waiters_lock:
            waiters = [
                entry[0] for entry in self._callback_waiters.values()
            ]
            self._callback_waiters.clear()
        for waiter in waiters:
            waiter.put(("__error__", {"message": str(error)}, b""))

    def _fail_callback_waiters_for_channel(
        self,
        channel_id: int,
        error: Exception,
    ):
        with self._callback_waiters_lock:
            matching = [
                (call_id, entry[0])
                for call_id, entry in self._callback_waiters.items()
                if entry[1] == channel_id
            ]
            for call_id, _ in matching:
                self._callback_waiters.pop(call_id, None)
        with self._async_callback_waiters_lock:
            async_matching = [
                (call_id, entry[0])
                for call_id, entry in self._async_callback_waiters.items()
                if entry[1] == channel_id
            ]
            for call_id, _ in async_matching:
                self._async_callback_waiters.pop(call_id, None)
        payload = ("__error__", {"message": str(error)}, b"")
        for _, waiter in matching + async_matching:
            waiter.put(payload)

    def _fail_all_async_callback_waiters(self, error: Exception):
        with self._async_callback_waiters_lock:
            waiters = [entry[0] for entry in self._async_callback_waiters.values()]
            self._async_callback_waiters.clear()
        for waiter in waiters:
            waiter.put(("__error__", {"message": str(error)}, b""))

    def _has_active_callback_waiters(self) -> bool:
        with self._callback_waiters_lock:
            return bool(self._callback_waiters)

    def _receive_command(self) -> tuple:
        """Read one framed command from the socket.

        Returns (cmd_name, cmd_data_dict, binary_sidecar_bytes).
        """
        msg_type, json_payload, binary_payload = receive_frame(self.sock, self.max_payload)

        if msg_type != MSG_TYPE_COMMAND:
            raise ValueError(f"Expected command (type 0), got type {msg_type}")

        try:
            cmd_name, cmd_data, scheduling, owner = decode_command_envelope(json_payload)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise FrameValidationError("Malformed command envelope") from error
        input_bytes = retained_command_bytes(cmd_data, len(json_payload) + len(binary_payload))
        return cmd_name, cmd_data, binary_payload, scheduling, owner, input_bytes

    def _send_response(self, resp_name: str, resp_data: dict, binary: bytes, channel_id: int | None = None):
        """Encode and send a framed response."""
        # A name outside the generated set cannot be decoded by `WorkerResponse`
        # on the host, so the host would fail with a decode error naming
        # nothing useful. Failing here names the offending response instead.
        if resp_name not in RESPONSE_CASES:
            raise ValueError(f"No WorkerResponse case declares the name {resp_name!r}")
        data = dict(resp_data)
        if resp_name in ("error", "pythonError"):
            if "message" in data:
                data["message"] = self._bounded_diagnostic(data["message"], 16 * 1024)
            if "traceback" in data and data["traceback"] is not None:
                data["traceback"] = self._bounded_diagnostic(data["traceback"], 64 * 1024)
            if "type" in data:
                data["type"] = self._bounded_diagnostic(data["type"], 256)
        if resp_name == "healthy":
            data.setdefault("protocolVersion", CURRENT_PROTOCOL_VERSION)
        if channel_id is not None:
            if resp_name in STREAM_CHANNEL_RESPONSES:
                data.setdefault("streamChannelID", channel_id)
            elif resp_name in SESSION_ROUTED_RESPONSES:
                data.setdefault("controlChannelID", channel_id)
            else:
                data.setdefault("channelID", channel_id)
        resp = {resp_name: data}
        json_bytes = json.dumps(resp, separators=(",", ":"), default=_json_default).encode("utf-8")
        actual_bytes = len(json_bytes) + len(binary)
        if actual_bytes > self.max_payload:
            # The producer owns the same ceiling as its reader. Never poison
            # a healthy endpoint by sending an undecodable oversized response.
            refusal = {"maxBytes": self.max_payload, "actualBytes": actual_bytes}
            if channel_id is not None:
                refusal["channelID"] = channel_id
            json_bytes = json.dumps({"payloadTooLarge": refusal}, separators=(",", ":")).encode("utf-8")
            resp_name = "payloadTooLarge"
            binary = b""
            if len(json_bytes) > self.max_payload:
                self._retire_failed_endpoint()
                raise FrameValidationError("Response ceiling cannot carry a typed refusal")
        frame = encode_frame(MSG_TYPE_RESPONSE, json_bytes, binary)
        owner = int(data.get("streamChannelID", data.get("controlChannelID", data.get("channelID", 0))))
        progress = resp_name not in ("handle", "result", "streamChunk", "duplexOpened")
        timeout = float(self.ipc_config.get("sendTimeout", 60))
        deadline = None if timeout <= 0 else time.monotonic() + timeout
        try:
            with self.response_writer.frame(owner, len(frame), progress, deadline):
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("writer deadline expired before sending")
                else:
                    remaining = None
                send_all_shared(self.sock, frame, timeout=remaining)
        except BaseException:
            self._retire_failed_endpoint()
            raise
        return resp_name

    def _handle_command(self, cmd_name: str, cmd_data: dict) -> tuple:
        """Dispatch a command, returning (resp_name, resp_data, binary)."""
        handler = COMMAND_DISPATCH.get(cmd_name)
        if handler is not None:
            return handler(self, cmd_data)
        # A name the Swift enum declares but this worker does not implement is
        # a different failure from a name that does not exist at all, and the
        # two used to be indistinguishable.
        if cmd_name in COMMAND_CASES:
            return "error", {
                "code": "internalError",
                "message": f"Command not implemented by the Python worker: {cmd_name}",
            }, b""
        return "error", {"code": "internalError", "message": f"Unknown command: {cmd_name}"}, b""

    def _handle_health_check(self, cmd_data: dict) -> tuple:
        return "healthy", {"protocolVersion": CURRENT_PROTOCOL_VERSION}, b""

    def _handle_describe_capabilities(self, cmd_data: dict) -> tuple:
        declaration = worker_capability_declaration(
            CURRENT_PROTOCOL_VERSION, self.duplex_sessions.transport_name
        )
        declaration["features"].append("eval.result.v1")
        return "capabilities", declaration, b""

    def _handle_shutdown(self, cmd_data: dict) -> tuple:
        self.running = False
        self.duplex_sessions.shutdown_all()
        return "success", {}, b""

    def _handle_duplex_open(self, cmd_data: dict) -> tuple:
        response, session = self.duplex_sessions.open(cmd_data)
        self.execution_admission.bind_duplex(session)
        return "duplexOpened", response, b""

    def _handle_duplex_application_control(self, cmd_data: dict) -> tuple:
        self.duplex_sessions.session(cmd_data["sessionID"]).application_control(
            int(cmd_data["controlSequence"]),
            str(cmd_data["kind"]),
            cmd_data.get("payload"),
            cmd_data.get("acknowledgedOutputThrough"),
        )
        return "success", {}, b""

    def _handle_duplex_interrupt(self, cmd_data: dict) -> tuple:
        self.duplex_sessions.session(cmd_data["sessionID"]).interrupt(
            int(cmd_data["controlSequence"]),
            str(cmd_data["interruptionID"]),
            str(cmd_data["reason"]),
            cmd_data.get("consumedOutputThrough"),
        )
        return "success", {}, b""

    def _handle_duplex_output_acknowledged(self, cmd_data: dict) -> tuple:
        self.duplex_sessions.session(
            cmd_data["sessionID"]
        ).output_acknowledged(
            int(cmd_data["controlSequence"]),
            cmd_data["consumedThrough"],
        )
        return "success", {}, b""

    def _handle_duplex_cancel(self, cmd_data: dict) -> tuple:
        self.duplex_sessions.session(cmd_data["sessionID"]).cancel(
            int(cmd_data["controlSequence"]),
            str(cmd_data["reason"]),
        )
        return "success", {}, b""

    def _handle_duplex_close(self, cmd_data: dict) -> tuple:
        self.duplex_sessions.close(
            cmd_data["sessionID"], int(cmd_data["controlChannelID"]),
            int(cmd_data["controlSequence"]),
        )
        return "success", {}, b""

    def _handle_stream_cancel(self, cmd_data: dict) -> tuple:
        self.execution_admission.cancel_stream(int(cmd_data.get("streamChannelID", 0) or 0))
        return "success", {}, b""

    # -----------------------------------------------------------------------
    # eval
    # -----------------------------------------------------------------------

    def _execute_eval(self, cmd_data: dict, result_encoding=None) -> tuple:
        code = textwrap.dedent(cmd_data.get("code", ""))
        bindings = cmd_data.get("bindings", {})

        try:
            self.namespace.pop("__result__", None)

            # Add bindings
            for name, descriptor in bindings.items():
                obj = self._resolve_handle(descriptor)
                self.namespace[name] = obj

            # Parse AST to handle trailing expressions like a REPL
            try:
                tree = ast.parse(code)
            except SyntaxError as original:
                if result_encoding is None:
                    raise
                try:
                    tree = ast.parse("(" + code + ")")
                except SyntaxError:
                    raise original
            result = None

            if tree.body:
                last = tree.body[-1]
                if isinstance(last, ast.Expr):
                    # Compile and exec everything except the last expression
                    if len(tree.body) > 1:
                        stmts = ast.Module(body=tree.body[:-1], type_ignores=[])
                        exec(compile(stmts, "<exec>", "exec"), self.namespace, self.namespace)
                    # Eval the last expression
                    expr = ast.Expression(body=last.value)
                    result = eval(compile(expr, "<eval>", "eval"), self.namespace, self.namespace)
                else:
                    exec(compile(code, "<exec>", "exec"), self.namespace, self.namespace)
            else:
                exec(compile(code, "<exec>", "exec"), self.namespace, self.namespace)

            if result is None:
                result = self.namespace.get("__result__")
            if result_encoding is not None:
                if result_encoding == "json":
                    encoded = json.dumps([result]).encode("utf-8")
                elif result_encoding == "pickle":
                    encoded = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
                else:
                    raise ValueError("Unknown evalResult encoding")
                if len(encoded) > self.ipc_config.get("maxPickleBytes", 16 * 1024 * 1024):
                    return "payloadTooLarge", {"maxBytes": self.ipc_config.get("maxPickleBytes", 16 * 1024 * 1024), "actualBytes": len(encoded)}, b""
                return "result", {"_0": ""}, encoded

            # Like the native worker, eval always answers with a handle — to
            # None for statement-only code — because the host decodes an eval
            # response as a handle (RC-POOL-013).
            handle_id = str(uuid.uuid4()).upper()
            with self.object_store_lock:
                self.object_store[handle_id] = result
            descriptor = {
                "id": handle_id,
                "processID": {"worker": {"index": self.worker_id, "generation": 1}},
                "isShared": False,
            }
            return "handle", {"_0": descriptor}, b""

        except Exception as e:
            return self._make_python_error(e)

    # -----------------------------------------------------------------------
    # invoke / invokeResult
    # -----------------------------------------------------------------------

    def _execute_invoke(self, cmd_data: dict, pickle_result: bool) -> tuple:
        module_name = cmd_data.get("module", "")
        function_name = cmd_data.get("function", "")
        args_desc = cmd_data.get("args", [])
        kwargs_desc = cmd_data.get("kwargs", {})

        try:
            import importlib
            mod = importlib.import_module(module_name)
            func = getattr(mod, function_name)

            args = [self._resolve_value_descriptor(a) for a in args_desc]
            kwargs = {k: self._resolve_value_descriptor(v) for k, v in kwargs_desc.items()}

            result = func(*args, **kwargs)

            if pickle_result:
                pickled = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
                return "result", {"_0": ""}, pickled
            else:
                handle_id = str(uuid.uuid4()).upper()
                with self.object_store_lock:
                    self.object_store[handle_id] = result
                descriptor = {
                    "id": handle_id,
                    "processID": {"worker": {"index": self.worker_id, "generation": 1}},
                    "isShared": False,
                }
                return "handle", {"_0": descriptor}, b""

        except Exception as e:
            return self._make_python_error(e)

    # -----------------------------------------------------------------------
    # method / methodResult
    # -----------------------------------------------------------------------

    def _execute_method(self, cmd_data: dict, pickle_result: bool) -> tuple:
        target_desc = cmd_data.get("target", {})
        method_name = cmd_data.get("name", "")
        args_desc = cmd_data.get("args", [])
        kwargs_desc = cmd_data.get("kwargs", {})

        try:
            target = self._resolve_handle(target_desc)
            method = getattr(target, method_name)

            args = [self._resolve_value_descriptor(a) for a in args_desc]
            kwargs = {k: self._resolve_value_descriptor(v) for k, v in kwargs_desc.items()}

            result = method(*args, **kwargs)

            if pickle_result:
                pickled = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
                return "result", {"_0": ""}, pickled
            else:
                handle_id = str(uuid.uuid4()).upper()
                with self.object_store_lock:
                    self.object_store[handle_id] = result
                descriptor = {
                    "id": handle_id,
                    "processID": {"worker": {"index": self.worker_id, "generation": 1}},
                    "isShared": False,
                }
                return "handle", {"_0": descriptor}, b""

        except Exception as e:
            return self._make_python_error(e)

    # -----------------------------------------------------------------------
    # Streaming
    # -----------------------------------------------------------------------

    def _execute_stream(self, cmd_name: str, cmd_data: dict):
        """Execute a streaming command, sending streamChunk/streamEnd frames."""
        self._order_after_delivered_side_commands(cmd_name)
        channel_id = self._command_channel_id(cmd_name, cmd_data)
        self.streaming_active = True
        self.abort_requested = False
        self.active_stream_channel.channel_id = channel_id
        self.active_stream_channel.started_ns = time.monotonic_ns()
        paced = self.stream_credit.begin(channel_id)

        def stop_waiting() -> bool:
            return self.abort_requested or self._is_stream_cancelled(channel_id) or not self.running

        try:
            if stop_waiting():
                self._send_response("streamEnd", {}, b"", channel_id=channel_id)
                return
            # Generators may report progress through swift_bridge.progress.
            self._ensure_swift_bridge()
            iterator = self._get_stream_iterator(cmd_name, cmd_data)

            aborted = False
            while True:
                if self.abort_requested:
                    aborted = True
                    break
                if self._is_stream_cancelled(channel_id):
                    break
                # A paced stream holds one credit before asking for a value,
                # so the host never has more frames in flight than it granted.
                if paced and not self.stream_credit.acquire(channel_id, stop_waiting):
                    continue
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                pickled = pickle.dumps(item, protocol=pickle.HIGHEST_PROTOCOL)
                self._send_response("streamChunk", {"_0": ""}, pickled, channel_id=channel_id)

            if aborted:
                # An abort truncates the stream; ending it with streamEnd would
                # present the prefix as complete (RC-STREAM-009).
                self._send_response(
                    "error",
                    {"code": "executionError", "message": "stream aborted before the producer finished"},
                    b"",
                    channel_id=channel_id,
                )
            else:
                self._send_response("streamEnd", {}, b"", channel_id=channel_id)

        except Exception as e:
            resp_name, resp_data, resp_binary = self._make_python_error(e)
            self._send_response(resp_name, resp_data, resp_binary, channel_id=channel_id)
        finally:
            self.stream_credit.end(channel_id)
            self.streaming_active = False
            self.abort_requested = False
            self._clear_stream_cancel(channel_id)
            self.active_stream_channel.channel_id = 0
            self.active_stream_channel.started_ns = 0

    def _signal_stream_cancel(self, channel_id: int):
        with self.cancel_flags_lock:
            flag = self.cancel_flags.setdefault(channel_id, threading.Event())
            flag.set()
        self.stream_credit.wake_all()

    def _clear_stream_cancel(self, channel_id: int):
        with self.cancel_flags_lock:
            self.cancel_flags.pop(channel_id, None)

    def _is_stream_cancelled(self, channel_id: int) -> bool:
        with self.cancel_flags_lock:
            flag = self.cancel_flags.get(channel_id)
            return bool(flag and flag.is_set())

    def _get_stream_iterator(self, cmd_name: str, cmd_data: dict):
        if cmd_name == "evalStream":
            code = textwrap.dedent(cmd_data.get("code", ""))
            bindings = cmd_data.get("bindings", {})
            for name, descriptor in bindings.items():
                self.namespace[name] = self._resolve_handle(descriptor)
            # Like eval: statements run first and the trailing expression is
            # the iterable, as the native worker accepts.
            tree = ast.parse(code)
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                if len(tree.body) > 1:
                    prelude = ast.Module(body=tree.body[:-1], type_ignores=[])
                    exec(compile(prelude, "<exec>", "exec"), self.namespace, self.namespace)
                expression = ast.Expression(body=tree.body[-1].value)
                result = eval(compile(expression, "<eval>", "eval"), self.namespace, self.namespace)
            else:
                result = eval(compile(code, "<eval>", "eval"), self.namespace, self.namespace)
            return iter(result)

        if cmd_name == "methodStream":
            target = self._resolve_handle(cmd_data.get("target", {}))
            method = getattr(target, cmd_data.get("name", ""))
            args = [self._resolve_value_descriptor(a) for a in cmd_data.get("args", [])]
            kwargs = {k: self._resolve_value_descriptor(v) for k, v in cmd_data.get("kwargs", {}).items()}
            return iter(method(*args, **kwargs))

        if cmd_name == "invokeStream":
            import importlib
            mod = importlib.import_module(cmd_data.get("module", ""))
            func = getattr(mod, cmd_data.get("function", ""))
            args = [self._resolve_value_descriptor(a) for a in cmd_data.get("args", [])]
            kwargs = {k: self._resolve_value_descriptor(v) for k, v in cmd_data.get("kwargs", {}).items()}
            return iter(func(*args, **kwargs))

        raise ValueError(f"Unknown stream command: {cmd_name}")

    # -----------------------------------------------------------------------
    # Object store
    # -----------------------------------------------------------------------

    def _store_object(self, cmd_data: dict) -> tuple:
        pickle_data = cmd_data.get("pickle", b"")
        if isinstance(pickle_data, str):
            import base64
            pickle_data = base64.b64decode(pickle_data)
        try:
            obj = pickle.loads(pickle_data)
            handle_id = str(uuid.uuid4()).upper()
            with self.object_store_lock:
                self.object_store[handle_id] = obj
            descriptor = {
                "id": handle_id,
                "processID": {"worker": {"index": self.worker_id, "generation": 1}},
                "isShared": False,
            }
            return "handle", {"_0": descriptor}, b""
        except Exception as e:
            return self._make_python_error(e)

    def _release_object(self, cmd_data: dict) -> tuple:
        handle_id = cmd_data.get("id", "")
        with self.object_store_lock:
            if self.control_object_pins.get(handle_id, 0):
                self.deferred_object_releases.add(handle_id)
            else:
                self.object_store.pop(handle_id, None)
        return "success", {}, b""

    # -----------------------------------------------------------------------
    # Shared memory — no-ops for VM workers
    #
    # POSIX shm_open/mmap cannot cross VM boundaries (host and guest have
    # separate kernel address spaces). The pool's configureSpawnedWorker
    # sends attachSharedMemory to all workers; for VM workers it is a
    # harmless no-op. OOB streaming uses SocketOOBStreamBuffer over vsock.
    # -----------------------------------------------------------------------

    def _attach_shared_memory(self, cmd_data: dict) -> tuple:
        return "success", {}, b""

    def _get_array_info(self, cmd_data: dict) -> tuple:
        handle_id = cmd_data.get("handleID", "")
        with self.object_store_lock:
            found = handle_id in self.object_store
            obj = self.object_store.get(handle_id)
        if not found:
            return "error", {"code": "handleNotFound", "message": f"Handle {handle_id} not found"}, b""
        try:
            import numpy as np
            if isinstance(obj, np.ndarray):
                return "arrayInfo", {
                    "shape": list(obj.shape),
                    "dtype": str(obj.dtype),
                    "byteSize": obj.nbytes,
                }, b""
        except ImportError:
            pass
        actual_type = type(obj).__name__
        return "notAnArray", {"handleID": handle_id, "actualType": actual_type}, b""

    def _copy_to_shared(self, cmd_data: dict) -> tuple:
        return "error", {"code": "internalError", "message": "copyToShared: host and guest have separate kernel address spaces; use OOB streaming over vsock instead"}, b""

    # -----------------------------------------------------------------------
    # Resource limits
    # -----------------------------------------------------------------------

    def _set_resource_limits(self, cmd_data: dict) -> tuple:
        max_memory = cmd_data.get("maxMemoryBytes")
        if max_memory is not None:
            try:
                import resource
                resource.setrlimit(resource.RLIMIT_AS, (max_memory, max_memory))
            except Exception as e:
                return "error", {"code": "resourceError", "message": str(e)}, b""
        return "success", {}, b""

    # -----------------------------------------------------------------------
    # Callbacks — bidirectional IPC with Swift host
    #
    # Python calls swift_bridge.call(name, *args) → worker sends
    # callbackInvocation → Swift runs handler → sends callbackResult back.
    # Python calls swift_bridge.call_async(name, *args) → worker sends
    # callbackAsyncInvocation → Swift runs handler → resolves a Future.
    # Nested commands from the Swift handler are dispatched inline.
    # -----------------------------------------------------------------------

    def _register_callback(self, cmd_data: dict) -> tuple:
        name = cmd_data.get("name", "")
        self._ensure_swift_bridge()
        self._registered_callbacks.add(name)
        return "success", {}, b""

    def _unregister_callback(self, cmd_data: dict) -> tuple:
        name = cmd_data.get("name", "")
        self._registered_callbacks.discard(name)
        return "success", {}, b""

    def _ensure_swift_bridge(self):
        """Install the swift_bridge module into sys.modules if not already present."""
        if self._swift_bridge_installed:
            return
        self._swift_bridge_installed = True

        import types
        mod = types.ModuleType("swift_bridge")
        worker_ref = self  # prevent GC

        def call(name, *args, **kwargs):
            if kwargs:
                raise TypeError(
                    "swift_bridge.call does not support keyword arguments for ProcessPool callbacks"
                )
            return worker_ref._execute_callback_via_ipc(name, list(args))

        def call_async(name, *args, **kwargs):
            import concurrent.futures

            future = concurrent.futures.Future()
            if kwargs:
                future.set_exception(
                    TypeError(
                        "swift_bridge.call_async does not support keyword arguments for ProcessPool callbacks"
                    )
                )
                return future

            try:
                call_id = worker_ref._start_async_callback_via_ipc(name, list(args))
            except BaseException as exc:
                future.set_exception(exc)
                return future

            def _wait_for_swift_callback():
                try:
                    result = worker_ref._wait_async_callback_via_ipc(call_id)
                except BaseException as exc:
                    if not future.cancelled():
                        future.set_exception(exc)
                else:
                    if not future.cancelled():
                        future.set_result(result)

            thread = threading.Thread(
                target=_wait_for_swift_callback,
                name=f"swift_bridge.call_async({name})",
                daemon=True,
            )
            thread.start()
            return future

        def call_stream(name, *args):
            call_id = worker_ref._execute_callback_via_ipc("__swift_stream_init__", [name] + list(args))
            return _SwiftStreamIterator(worker_ref, call_id)

        def is_registered(name):
            return name in worker_ref._registered_callbacks

        def registered_names():
            return list(worker_ref._registered_callbacks)

        def progress(hint=None):
            channel_id = int(getattr(worker_ref.active_stream_channel, "channel_id", 0) or 0)
            if channel_id == 0:
                return None
            started_ns = int(getattr(worker_ref.active_stream_channel, "started_ns", 0) or 0)
            elapsed_ms = 0 if started_ns == 0 else int((time.monotonic_ns() - started_ns) / 1_000_000)
            data = {"elapsedMs": elapsed_ms}
            if hint is not None:
                data["hint"] = str(hint)
            # Progress occupies consumer capacity, so a paced stream spends
            # credit on it too.
            if worker_ref.stream_credit.is_paced(channel_id) and not worker_ref.stream_credit.acquire(
                channel_id,
                lambda: worker_ref.abort_requested
                or worker_ref._is_stream_cancelled(channel_id)
                or not worker_ref.running,
            ):
                return None
            worker_ref._send_response("streamProgress", data, b"", channel_id=channel_id)
            return None

        def check_cancel():
            channel_id = int(getattr(worker_ref.active_stream_channel, "channel_id", 0) or 0)
            if channel_id and worker_ref._is_stream_cancelled(channel_id):
                raise KeyboardInterrupt()
            return None

        mod.call = call
        mod.call_async = call_async
        mod.call_stream = call_stream
        mod.is_registered = is_registered
        mod.registered_names = registered_names
        mod.progress = progress
        mod.check_cancel = check_cancel
        sys.modules["swift_bridge"] = mod
        self.namespace["swift_bridge"] = mod

    def _execute_callback_via_ipc(self, name: str, args: list):
        """Send callbackInvocation to Swift, block for result.

        The dispatcher thread is the sole receive owner. Callback results
        are routed here through a per-callId queue; nested commands are
        dispatched by the normal command pools while this waiter blocks.
        """
        call_id = self._next_callback_call_id()
        channel_id = self._current_callback_channel_id()
        waiter = self._register_callback_waiter(call_id, channel_id)

        args_json = json.dumps(args, default=_json_default).encode("utf-8")
        self._send_response("callbackInvocation", {
            "callId": call_id,
            "name": name,
            "argsPickle": "",
        }, args_json, channel_id=channel_id)

        try:
            while True:
                cmd_name, cmd_data, binary = waiter.get(
                    timeout=self.ipc_config.get("receiveTimeout", 30)
                )

                if cmd_name == "__error__":
                    raise RuntimeError(cmd_data.get("message", "callback waiter failed"))

                if cmd_name == "callbackResult":
                    result_call_id = cmd_data.get("callId", -1)
                    if result_call_id != call_id:
                        raise RuntimeError(
                            f"Callback callId mismatch: expected {call_id}, got {result_call_id}"
                        )
                    if name == "__swift_stream_init__":
                        return call_id
                    result_pickle = cmd_data.get("pickle", b"")
                    if isinstance(result_pickle, str):
                        result_pickle = result_pickle.encode("utf-8")
                    if not result_pickle:
                        result_pickle = binary if binary else b"[null]"
                    result_array = json.loads(result_pickle)
                    return result_array[0] if isinstance(result_array, list) and result_array else result_array

                if cmd_name == "callbackError":
                    error_call_id = cmd_data.get("callId", -1)
                    if error_call_id != call_id:
                        raise RuntimeError(
                            f"Callback callId mismatch: expected {call_id}, got {error_call_id}"
                        )
                    err_type = cmd_data.get("type", "RuntimeError")
                    err_msg = cmd_data.get("message", "Unknown callback error")
                    raise RuntimeError(f"[{err_type}] {err_msg}")

                raise RuntimeError(f"Unexpected command during callback: {cmd_name}")
        except queue.Empty:
            raise TimeoutError(f"Timed out waiting for callback result callId={call_id}")
        finally:
            self._unregister_callback_waiter(call_id)

    def _start_async_callback_via_ipc(self, name: str, args: list) -> int:
        """Send callbackAsyncInvocation to Swift and return a Future call id."""
        call_id = self._next_callback_call_id()
        channel_id = self._current_callback_channel_id()
        self._register_async_callback_waiter(call_id, channel_id)

        args_json = json.dumps(args, default=_json_default).encode("utf-8")
        try:
            self._send_response("callbackAsyncInvocation", {
                "callId": call_id,
                "name": name,
                "argsPickle": "",
            }, args_json, channel_id=channel_id)
        except BaseException:
            self._unregister_async_callback_waiter(call_id)
            raise
        return call_id

    def _wait_async_callback_via_ipc(self, call_id: int):
        """Wait for an async callback result without entering callback reentry routing."""
        entry = self._async_callback_waiter(call_id)
        if entry is None:
            raise RuntimeError(f"No pending async callback callId={call_id}")
        waiter, channel_id, _depth = entry

        try:
            while True:
                cmd_name, cmd_data, binary = waiter.get(
                    timeout=self.ipc_config.get("receiveTimeout", 30)
                )

                if cmd_name == "__error__":
                    raise RuntimeError(cmd_data.get("message", "async callback waiter failed"))

                if cmd_name == "callbackResult":
                    result_call_id = cmd_data.get("callId", -1)
                    if result_call_id != call_id:
                        raise RuntimeError(
                            f"Async callback callId mismatch: expected {call_id}, got {result_call_id}"
                        )
                    self._send_response("callbackAsyncAck", {"callId": call_id}, b"", channel_id=channel_id)
                    result_pickle = cmd_data.get("pickle", b"")
                    if isinstance(result_pickle, str):
                        result_pickle = result_pickle.encode("utf-8")
                    if not result_pickle:
                        result_pickle = binary if binary else b"[null]"
                    result_array = json.loads(result_pickle)
                    return result_array[0] if isinstance(result_array, list) and result_array else result_array

                if cmd_name == "callbackError":
                    error_call_id = cmd_data.get("callId", -1)
                    if error_call_id != call_id:
                        raise RuntimeError(
                            f"Async callback callId mismatch: expected {call_id}, got {error_call_id}"
                        )
                    self._send_response("callbackAsyncAck", {"callId": call_id}, b"", channel_id=channel_id)
                    err_type = cmd_data.get("type", "RuntimeError")
                    err_msg = cmd_data.get("message", "Unknown callback error")
                    raise RuntimeError(f"[{err_type}] {err_msg}")

                raise RuntimeError(f"Unexpected command during async callback: {cmd_name}")
        except queue.Empty:
            raise TimeoutError(f"Timed out waiting for async callback result callId={call_id}")
        finally:
            self._unregister_async_callback_waiter(call_id)

    def _execute_stream_next_via_ipc(self, call_id: int):
        channel_id = self._current_callback_channel_id()
        waiter = self._register_callback_waiter(call_id, channel_id)
        self._send_response("callbackStreamNext", {"callId": call_id}, b"", channel_id=channel_id)
        try:
            while True:
                cmd_name, cmd_data, binary = waiter.get(
                    timeout=self.ipc_config.get("receiveTimeout", 30)
                )
                if cmd_name == "__error__":
                    raise RuntimeError(cmd_data.get("message", "callback stream waiter failed"))
                if cmd_name == "callbackStreamChunk":
                    chunk_call_id = cmd_data.get("callId", -1)
                    if chunk_call_id != call_id:
                        raise RuntimeError(
                            f"Stream next callId mismatch: expected {call_id}, got {chunk_call_id}"
                        )
                    pickle_data = cmd_data.get("pickle", b"")
                    if isinstance(pickle_data, str):
                        import base64
                        pickle_data = base64.b64decode(pickle_data)
                    if not pickle_data:
                        pickle_data = binary
                    return pickle.loads(pickle_data)
                if cmd_name == "callbackStreamEnd":
                    end_call_id = cmd_data.get("callId", -1)
                    if end_call_id != call_id:
                        raise RuntimeError(
                            f"Stream end callId mismatch: expected {call_id}, got {end_call_id}"
                        )
                    raise StopIteration
                if cmd_name == "callbackStreamError":
                    err_call_id = cmd_data.get("callId", -1)
                    if err_call_id != call_id:
                        raise RuntimeError(
                            f"Stream error callId mismatch: expected {call_id}, got {err_call_id}"
                        )
                    err_type = cmd_data.get("type", "RuntimeError")
                    err_msg = cmd_data.get("message", "Stream error")
                    if "StopIteration" in err_type:
                        raise StopIteration
                    raise RuntimeError(f"[{err_type}] {err_msg}")
                raise RuntimeError(f"Unexpected command during stream next: {cmd_name}")
        except queue.Empty:
            raise TimeoutError(f"Timed out waiting for callback stream callId={call_id}")
        finally:
            self._unregister_callback_waiter(call_id)

    # -----------------------------------------------------------------------
    # Handle resolution
    # -----------------------------------------------------------------------

    def _resolve_handle(self, descriptor: dict) -> object:
        handle_id = descriptor.get("id", "")
        with self.object_store_lock:
            if handle_id not in self.object_store:
                raise HandleNotFoundError(f"Handle not found: {handle_id}")
            return self.object_store[handle_id]

    def _resolve_value_descriptor(self, desc: dict) -> object:
        if "handle" in desc:
            return self._resolve_handle(desc["handle"])
        if "pickle" in desc:
            data = desc["pickle"].get("_0", b"")
            if isinstance(data, str):
                import base64
                data = base64.b64decode(data)
            return pickle.loads(data)
        raise ValueError(f"Unknown value descriptor: {desc}")

    # -----------------------------------------------------------------------
    # Error formatting
    # -----------------------------------------------------------------------

    def _make_python_error(self, exc: Exception) -> tuple:
        if isinstance(exc, HandleNotFoundError):
            # The native worker reports an unknown handle as a wire error,
            # not a Python exception; callers see the same error either way.
            return "error", {"code": "executionError", "message": str(exc)}, b""
        exc_type = self._bounded_diagnostic(type(exc).__name__, 256)
        exc_msg = self._bounded_diagnostic(str(exc), 16 * 1024)
        exc_tb = self._bounded_diagnostic(traceback.format_exc(), 64 * 1024)
        return "pythonError", {
            "type": exc_type,
            "message": exc_msg,
            "traceback": exc_tb,
        }, b""

    @staticmethod
    def _bounded_diagnostic(text, maximum_bytes):
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= maximum_bytes:
            return text
        marker = b"\n[diagnostic truncated]\n"
        available = maximum_bytes - len(marker)
        head = available * 3 // 4
        return (encoded[:head].decode("utf-8", errors="ignore") + marker.decode()
                + encoded[-(available - head):].decode("utf-8", errors="ignore"))


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

# Command name -> handler. Every key is checked against the generated
# `COMMAND_CASES` below, so a name that does not correspond to a `WorkerCommand`
# case raises at import time in the guest rather than reporting "unknown
# command" at runtime, months later, to a host that assumed it was supported.
COMMAND_DISPATCH = {
    "healthCheck": Worker._handle_health_check,
    "describeCapabilities": Worker._handle_describe_capabilities,
    "duplexOpen": Worker._handle_duplex_open,
    "duplexApplicationControl":
        Worker._handle_duplex_application_control,
    "duplexInterrupt": Worker._handle_duplex_interrupt,
    "duplexOutputAcknowledged":
        Worker._handle_duplex_output_acknowledged,
    "duplexCancel": Worker._handle_duplex_cancel,
    "duplexClose": Worker._handle_duplex_close,
    "shutdown": Worker._handle_shutdown,
    "eval": Worker._execute_eval,
    "evalResult": lambda self, data: self._execute_eval(data, data.get("encoding", "pickle")),
    "invoke": lambda self, data: self._execute_invoke(data, pickle_result=False),
    "invokeResult": lambda self, data: self._execute_invoke(data, pickle_result=True),
    "method": lambda self, data: self._execute_method(data, pickle_result=False),
    "methodResult": lambda self, data: self._execute_method(data, pickle_result=True),
    "streamCancel": Worker._handle_stream_cancel,
    "store": Worker._store_object,
    "release": Worker._release_object,
    "setResourceLimits": Worker._set_resource_limits,
    "getArrayInfo": Worker._get_array_info,
    "copyToShared": Worker._copy_to_shared,
    "attachSharedMemory": Worker._attach_shared_memory,
    "registerCallback": Worker._register_callback,
    "unregisterCallback": Worker._unregister_callback,
}

_undeclared_commands = sorted(set(COMMAND_DISPATCH) - COMMAND_CASES)
if _undeclared_commands:
    raise ImportError(
        "swiftpython_worker dispatches commands that no WorkerCommand case declares: "
        + ", ".join(_undeclared_commands)
    )


# ---------------------------------------------------------------------------
# JSON serialization helper
# ---------------------------------------------------------------------------


def _json_default(obj):
    """Handle non-JSON-serializable types in response encoding."""
    if isinstance(obj, bytes):
        import base64
        return base64.b64encode(obj).decode("ascii")
    if isinstance(obj, uuid.UUID):
        return str(obj).upper()
    raise TypeError(f"Not JSON serializable: {type(obj)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: swiftpython_worker.py <socket_path_or_--vsock> <worker_id> [ipc_config_json] [side_socket]",
            file=sys.stderr,
        )
        sys.exit(1)

    socket_arg = sys.argv[1]
    worker_id = int(sys.argv[2])

    ipc_config = {}
    if len(sys.argv) >= 4:
        try:
            ipc_config = json.loads(sys.argv[3])
        except (json.JSONDecodeError, ValueError):
            pass

    # Connect to host
    side_sock = None
    transport_mode = "uds"
    if socket_arg == "--vsock-listen":
        transport_mode = "vsock"
        # Invoked by supervisor as:
        #   swiftpython_worker.py --vsock-listen <worker_id> <port> <ipc_config> [side_port]
        # Worker LISTENS on vsock ports; host connects via device.connect(toPort:).
        if len(sys.argv) < 4:
            print("Usage: swiftpython_worker.py --vsock-listen <worker_id> <port> [ipc_config] [side_port]", file=sys.stderr)
            sys.exit(1)
        worker_id = int(sys.argv[2])
        port = int(sys.argv[3])
        ipc_config_str = sys.argv[4] if len(sys.argv) >= 5 else "{}"
        try:
            ipc_config = json.loads(ipc_config_str)
        except (json.JSONDecodeError, ValueError):
            ipc_config = {}
        # Both listeners exist before either accept, so the host's side-port
        # connect never races the main-port accept.
        main_server = bind_vsock_listener(port)
        side_server = None
        side_port = None
        if len(sys.argv) >= 6:
            side_port = int(sys.argv[5])
            try:
                side_server = bind_vsock_listener(side_port)
            except BaseException:
                main_server.close()
                raise
        print(f"[worker {worker_id}] listening on vsock port {port}"
              + (f" and side port {side_port}" if side_server is not None else ""),
              file=sys.stderr, flush=True)
        try:
            sock = accept_vsock_listener(main_server)
        except BaseException:
            if side_server is not None:
                side_server.close()
            raise
        print(f"[worker {worker_id}] host connected on main port {port}", file=sys.stderr, flush=True)
        if side_server is not None:
            side_sock = accept_vsock_listener(side_server)
            print(f"[worker {worker_id}] host connected on side port {side_port}", file=sys.stderr, flush=True)
    elif socket_arg == "--vsock":
        transport_mode = "vsock"
        # --vsock <worker_id> <cid> <port> [ipc_config] [side_port]
        if len(sys.argv) < 5:
            print("Usage: swiftpython_worker.py --vsock <worker_id> <cid> <port> [ipc_config] [side_port]", file=sys.stderr)
            sys.exit(1)
        cid = int(sys.argv[3])
        port = int(sys.argv[4])
        ipc_config_str = sys.argv[5] if len(sys.argv) >= 6 else "{}"
        try:
            ipc_config = json.loads(ipc_config_str)
        except (json.JSONDecodeError, ValueError):
            ipc_config = {}
        sock = connect_vsock(cid, port)
        # Side channel vsock port (optional)
        if len(sys.argv) >= 7:
            side_port = int(sys.argv[6])
            side_sock = connect_vsock(cid, side_port)
    else:
        # UDS mode (for local testing)
        sock = connect_uds(socket_arg)
        # Side channel UDS path (optional, 4th positional arg)
        if len(sys.argv) >= 5 and sys.argv[4]:
            try:
                side_sock = connect_uds(sys.argv[4])
            except Exception as e:
                print(f"[worker {worker_id}] side channel connect failed: {e}", file=sys.stderr, flush=True)

    worker = Worker(sock, worker_id, ipc_config, transport_mode)

    # Start side channel if connected
    if side_sock is not None:
        worker.start_side_channel(side_sock)

    try:
        worker.run()
    except Exception as e:
        print(f"Worker {worker_id} fatal error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    finally:
        worker.side_stopping = True
        sock.close()
        os._exit(0)


if __name__ == "__main__":
    main()
