"""Guest-side duplex sessions for the SwiftPython VM worker: media handshake
and envelopes, credit, the accelerator lane and the session manager."""

import _swiftpython_duplex as _duplex_helper
import base64
import collections
import hashlib
import hmac
import importlib
import json
import math
import pickle
import queue
import socket
import struct
import threading
import time
import traceback
import uuid
from swiftpython_frames import listen_vsock, recv_exact, send_all  # noqa: E402
from _swiftpython_duplex import capability_declaration  # noqa: E402
from _swiftpython_wire import (  # noqa: E402
    CURRENT_PROTOCOL_VERSION,
    DUPLEX_MAXIMUM_ACCELERATOR_LEASES,
    DUPLEX_MAXIMUM_ACCELERATOR_PROCESS_LANES,
    DUPLEX_MAXIMUM_ACCELERATOR_QUEUED_STEPS,
    DUPLEX_MAXIMUM_ACCELERATOR_RESIDENT_BYTES,
    DUPLEX_MAXIMUM_ACCELERATOR_RESIDENT_MODELS,
    DUPLEX_MAXIMUM_ACCELERATOR_SCHEDULING_WEIGHT,
    DUPLEX_MAXIMUM_ACCELERATOR_STATE_BYTES,
    DUPLEX_MAXIMUM_ACCELERATOR_STATE_ITEMS,
    DUPLEX_MAXIMUM_ACTIVE_SESSIONS,
    DUPLEX_MAXIMUM_CONTROL_PAYLOAD_BYTES,
    DUPLEX_MAXIMUM_CREDIT_FRAMES,
    DUPLEX_MAXIMUM_DESCRIPTOR_STRING_BYTES,
    DUPLEX_MAXIMUM_EGRESS_CREDIT_BYTES,
    DUPLEX_MAXIMUM_FORMATS,
    DUPLEX_MAXIMUM_FORMAT_METADATA_ENTRIES,
    DUPLEX_MAXIMUM_INGRESS_CREDIT_BYTES,
    DUPLEX_MAXIMUM_LOGICAL_MESSAGE_BYTES,
    DUPLEX_MAXIMUM_MEDIA_FRAME_BYTES,
    DUPLEX_MAXIMUM_OUTSTANDING_MESSAGES,
    DUPLEX_MAXIMUM_PYTHON_CONTROL_EVENTS,
    DUPLEX_MAXIMUM_PYTHON_INTERRUPTION_EVENTS,
    DUPLEX_MAXIMUM_VSOCK_MEDIA_FRAME_BYTES,
    DUPLEX_MEDIA_DIRECTIONS,
    DUPLEX_MEDIA_ENVELOPE_KINDS,
    DUPLEX_MEDIA_MAGIC,
    DUPLEX_MEDIA_PROTOCOL_VERSION,
    DUPLEX_MEDIA_ROLES,
    DUPLEX_SUPPORTED_REQUIRED_FLAG_MASK,
)


class _GuestDuplexError(RuntimeError):
    pass

class _GuestDuplexResourceError(_GuestDuplexError):
    pass

class _GuestDuplexAcceleratorResourceError(_GuestDuplexResourceError):
    pass

def _duplex_now_ns() -> int:
    return time.monotonic_ns()

def _duplex_uuid_bytes(value: str) -> bytes:
    return uuid.UUID(str(value)).bytes

def _duplex_handshake_body(
    *,
    session_id: str,
    worker_id: int,
    generation: int,
    sender_role: int,
    receiver_role: int,
    nonce_id: str,
    nonce: bytes,
    configuration_digest: bytes,
    configuration: dict,
) -> bytes:
    if len(nonce) != 32 or len(configuration_digest) != 32:
        raise _GuestDuplexError("media handshake nonce or digest has invalid length")
    return b"".join((
        struct.pack(
            "<IHBB",
            DUPLEX_MEDIA_MAGIC,
            int(configuration["mediaProtocolVersion"]),
            sender_role,
            receiver_role,
        ),
        _duplex_uuid_bytes(session_id),
        struct.pack("<iQ", worker_id, generation),
        _duplex_uuid_bytes(nonce_id),
        nonce,
        configuration_digest,
        struct.pack(
            "<IQIQQ",
            int(configuration["ingressCreditFrames"]),
            int(configuration["ingressCreditBytes"]),
            int(configuration["egressCreditFrames"]),
            int(configuration["egressCreditBytes"]),
            int(configuration["maxFrameBytes"]),
        ),
    ))

def _duplex_encode_handshake(secret: bytes, **identity) -> bytes:
    body = _duplex_handshake_body(**identity)
    if len(body) != 148 or len(secret) < 32:
        raise _GuestDuplexError("media handshake identity has invalid bounds")
    return body + hmac.new(secret, body, hashlib.sha256).digest()

def _duplex_authenticate_handshake(
    data: bytes,
    secret: bytes,
    **expected_identity,
):
    if len(data) != 180:
        raise _GuestDuplexError("media handshake length mismatch")
    body, proof = data[:148], data[148:]
    if not hmac.compare_digest(
        proof,
        hmac.new(secret, body, hashlib.sha256).digest(),
    ):
        raise _GuestDuplexError("media handshake proof mismatch")
    expected = _duplex_handshake_body(**expected_identity)
    if not hmac.compare_digest(body, expected):
        raise _GuestDuplexError("media handshake identity mismatch")

def _duplex_validate_flags(flags: int):
    unknown_required = (
        flags
        & 0xFF00
        & ~DUPLEX_SUPPORTED_REQUIRED_FLAG_MASK
    )
    if unknown_required:
        raise _GuestDuplexError("unknown required media flags")

def _duplex_read_envelope(
    sock: socket.socket,
    max_frame_bytes: int,
    media_protocol_version: int,
) -> dict:
    length_bytes = recv_exact(sock, 4)
    bytes_after_length = struct.unpack("<I", length_bytes)[0]
    # A v2 message chunk has a 56-byte body before its bounded payload. Keep
    # allocation bounded by the negotiated physical-frame ceiling.
    maximum = 20 + 56 + max_frame_bytes
    if bytes_after_length < 20 or bytes_after_length > maximum:
        raise _GuestDuplexError("media envelope length is outside bounds")
    rest = recv_exact(sock, bytes_after_length)
    kind, direction, flags, sequence, timestamp_ns = struct.unpack_from(
        "<BBHQQ", rest, 0
    )
    _duplex_validate_flags(flags)
    body = rest[20:]
    known_kinds = set(DUPLEX_MEDIA_ENVELOPE_KINDS.values())
    known_directions = set(DUPLEX_MEDIA_DIRECTIONS.values())
    if kind not in known_kinds or direction not in known_directions:
        raise _GuestDuplexError("unknown media envelope kind or direction")

    result = {
        "kind": kind,
        "direction": direction,
        "flags": flags,
        "sequence": sequence,
        "timestamp_ns": timestamp_ns,
    }
    if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["data"]:
        if sequence == 0 or len(body) < 16:
            raise _GuestDuplexError("invalid data media envelope")
        format_id, processed_raw, payload_length = struct.unpack_from(
            "<IQI", body, 0
        )
        payload = body[16:]
        if payload_length != len(payload) or payload_length > max_frame_bytes:
            raise _GuestDuplexError("data media payload length is invalid")
        result.update({
            "format_id": format_id,
            "processed_through":
                None if processed_raw == (1 << 64) - 1 else processed_raw,
            "payload": payload,
        })
    elif kind == DUPLEX_MEDIA_ENVELOPE_KINDS["messageChunk"]:
        if sequence == 0 or len(body) < 56:
            raise _GuestDuplexError("invalid message-chunk media envelope")
        (
            message_id,
            total_bytes,
            byte_offset,
            chunk_index,
            chunk_count_raw,
            format_id,
            processed_raw,
            payload_length,
        ) = (
            body[:16],
            *struct.unpack_from("<QQIIIQI", body, 16),
        )
        payload = body[56:]
        end = byte_offset + payload_length
        if (
            payload_length != len(payload)
            or payload_length > max_frame_bytes
            or end > total_bytes
            or (
                chunk_count_raw != (1 << 32) - 1
                and chunk_index >= chunk_count_raw
            )
        ):
            raise _GuestDuplexError("message-chunk fields exceed negotiated bounds")
        result.update({
            "message_id": str(uuid.UUID(bytes=message_id)).upper(),
            "total_bytes": total_bytes,
            "byte_offset": byte_offset,
            "chunk_index": chunk_index,
            "chunk_count": (
                None
                if chunk_count_raw == (1 << 32) - 1
                else chunk_count_raw
            ),
            "format_id": format_id,
            "processed_through": (
                None if processed_raw == (1 << 64) - 1 else processed_raw
            ),
            "payload": payload,
        })
    elif kind == DUPLEX_MEDIA_ENVELOPE_KINDS["messageAbort"]:
        if sequence != 0 or len(body) != 20:
            raise _GuestDuplexError("invalid message-abort media envelope")
        result.update({
            "message_id": str(uuid.UUID(bytes=body[:16])).upper(),
            "reason_code": struct.unpack_from("<I", body, 16)[0],
        })
    elif kind in (
        DUPLEX_MEDIA_ENVELOPE_KINDS["arenaReference"],
        DUPLEX_MEDIA_ENVELOPE_KINDS["arenaRelease"],
    ):
        raise _GuestDuplexError("shared-arena media is unavailable in the VM guest")
    elif kind == DUPLEX_MEDIA_ENVELOPE_KINDS["credit"]:
        if len(body) != 16:
            raise _GuestDuplexError("credit media envelope has invalid length")
        released_frames, released_bytes = struct.unpack("<QQ", body)
        result.update({
            "released_frames": released_frames,
            "released_bytes": released_bytes,
            "released_through": sequence,
        })
    elif kind == DUPLEX_MEDIA_ENVELOPE_KINDS["directionEnd"]:
        if body:
            raise _GuestDuplexError("direction-end media envelope has trailing bytes")
    elif kind == DUPLEX_MEDIA_ENVELOPE_KINDS["discontinuity"]:
        if len(body) != 20:
            raise _GuestDuplexError("discontinuity media envelope has invalid length")
        last_sequence, duration_ns, reason_code = struct.unpack("<QQI", body)
        if sequence == 0 or last_sequence < sequence:
            raise _GuestDuplexError("invalid discontinuity sequence range")
        result.update({
            "last_sequence": last_sequence,
            "duration_ns": duration_ns,
            "reason_code": reason_code,
        })
    elif body or sequence != 0:
        raise _GuestDuplexError("structural media envelope has invalid body")
    return result

def _duplex_encode_envelope(
    *,
    kind: int,
    direction: int,
    flags: int = 0,
    sequence: int = 0,
    timestamp_ns: int | None = None,
    format_id: int | None = None,
    processed_through: int | None = None,
    payload=None,
    released_frames: int | None = None,
    released_bytes: int | None = None,
    last_sequence: int | None = None,
    duration_ns: int | None = None,
    reason_code: int | None = None,
) -> tuple[bytes, memoryview | None]:
    _duplex_validate_flags(flags)
    timestamp = _duplex_now_ns() if timestamp_ns is None else int(timestamp_ns)
    payload_view = None
    body = b""
    if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["data"]:
        if sequence <= 0 or format_id is None:
            raise _GuestDuplexError("data media envelope has invalid sequence or format")
        payload_view = memoryview(payload).cast("B")
        processed = (
            (1 << 64) - 1
            if processed_through is None
            else int(processed_through)
        )
        body = struct.pack(
            "<IQI",
            int(format_id),
            processed,
            payload_view.nbytes,
        )
    elif kind == DUPLEX_MEDIA_ENVELOPE_KINDS["credit"]:
        body = struct.pack(
            "<QQ",
            int(released_frames or 0),
            int(released_bytes or 0),
        )
    elif kind == DUPLEX_MEDIA_ENVELOPE_KINDS["directionEnd"]:
        pass
    elif kind == DUPLEX_MEDIA_ENVELOPE_KINDS["discontinuity"]:
        if sequence <= 0 or last_sequence is None or last_sequence < sequence:
            raise _GuestDuplexError("invalid discontinuity sequence range")
        body = struct.pack(
            "<QQI",
            int(last_sequence),
            int(duration_ns or 0),
            int(reason_code or 0),
        )
    elif kind not in (
        DUPLEX_MEDIA_ENVELOPE_KINDS["ping"],
        DUPLEX_MEDIA_ENVELOPE_KINDS["pong"],
    ):
        raise _GuestDuplexError("unknown outbound media envelope kind")

    payload_bytes = 0 if payload_view is None else payload_view.nbytes
    bytes_after_length = 20 + len(body) + payload_bytes
    header = struct.pack(
        "<IBBHQQ",
        bytes_after_length,
        kind,
        direction,
        flags,
        int(sequence),
        timestamp,
    ) + body
    return header, payload_view

class _CreditByteArray(bytearray):
    """Owned ingress copy whose exporter lifetime returns cumulative credit."""

    def __init__(self, data: bytes, release):
        super().__init__(data)
        self._duplex_release = release

    def __del__(self):
        release = getattr(self, "_duplex_release", None)
        self._duplex_release = None
        if release is not None:
            release()

class _GuestAcceleratorLane:
    """Pinned bounded deficit-round-robin MLX lane for the guest worker."""

    def __init__(self):
        self.cv = threading.Condition()
        self.sessions = {}
        self.order = []
        self.cursor = 0
        self.thread = None
        self.stopping = False
        self.warmup_owner = None
        self.maintenance_owner = None
        self.thread_id = 0
        self.executing = False
        self.last_lane_name = ""
        self.last_phase = ""
        self.last_queue_wait_ns = 0
        self.last_execution_ns = 0
        self.completed_steps = 0
        self.rejected_steps = 0
        self.process_configuration = None

    @staticmethod
    def _shares_process_policy(first: dict, second: dict) -> bool:
        keys = (
            "laneName",
            "maximumActiveSessions",
            "maximumResidentModels",
            "maximumResidentBytes",
            "maximumSimultaneousLeases",
            "defaultModelTTLMilliseconds",
            "cacheClearMinimumIntervalMilliseconds",
            "maximumProcessLanes",
            "startupStressProbe",
            "softPressureRatioPermille",
            "throttlePressureRatioPermille",
            "shedPressureRatioPermille",
        )
        return all(first.get(key) == second.get(key) for key in keys)

    def register(self, token: int, configuration: dict):
        accelerator = configuration.get("accelerator", {"kind": "none"})
        if accelerator.get("kind") != "mlx":
            return
        with self.cv:
            maximum = int(accelerator["maximumActiveSessions"])
            if (
                self.stopping
                or token in self.sessions
                or len(self.sessions) >= maximum
            ):
                raise _GuestDuplexAcceleratorResourceError(
                    "guest MLX accelerator session admission exhausted"
                )
            if self.process_configuration is not None:
                if not self._shares_process_policy(
                    self.process_configuration,
                    accelerator,
                ):
                    raise _GuestDuplexAcceleratorResourceError(
                        "one guest worker generation cannot mix process-wide "
                        "MLX lane or residency policies"
                    )
            else:
                self.process_configuration = dict(accelerator)
            self.sessions[token] = {
                "configuration": accelerator,
                "queue": collections.deque(),
                "deficit": 0,
                "warmed": False,
                "cancelled": False,
            }
            self.order.append(token)
            self.last_lane_name = accelerator["laneName"]
            if self.thread is None:
                self.thread = threading.Thread(
                    target=self._run_loop,
                    name="swiftpython-vm-mlx-accelerator-lane",
                    daemon=True,
                )
                self.thread.start()
            self.cv.notify_all()

    def unregister(self, token: int):
        with self.cv:
            state = self.sessions.pop(token, None)
            self.order = [value for value in self.order if value != token]
            self.cursor = 0 if not self.order else self.cursor % len(self.order)
            if self.warmup_owner == token:
                self.warmup_owner = None
            if self.maintenance_owner == token:
                self.maintenance_owner = None
            self.cv.notify_all()
        if state is not None:
            for job in state["queue"]:
                job["error"] = RuntimeError(
                    "MLX accelerator step was cancelled before execution"
                )
                job["done"].set()

    def cancel(self, token: int):
        with self.cv:
            state = self.sessions.get(token)
            if state is None:
                return
            state["cancelled"] = True
            pending = list(state["queue"])
            state["queue"].clear()
            self.cv.notify_all()
        for job in pending:
            job["error"] = RuntimeError(
                "cancelled MLX step was ignored before execution"
            )
            job["done"].set()

    def begin_warmup(self, token: int):
        with self.cv:
            while (
                not self.stopping
                and token in self.sessions
                and not self.sessions[token]["cancelled"]
                and (
                    self.warmup_owner is not None
                    or self.maintenance_owner is not None
                    or self.executing
                )
            ):
                self.cv.wait()
            state = self.sessions.get(token)
            if self.stopping or state is None or state["cancelled"]:
                raise RuntimeError("duplex session is closed")
            if state["warmed"]:
                raise ValueError("MLX warm-up may complete exactly once")
            self.warmup_owner = token

    def finish_warmup(self, token: int, representative_shapes: int):
        with self.cv:
            state = self.sessions.get(token)
            if (
                representative_shapes <= 0
                or state is None
                or self.warmup_owner != token
            ):
                raise ValueError(
                    "MLX warm-up requires representative evaluated shapes"
                )
            state["warmed"] = True
            self.warmup_owner = None
            self.cv.notify_all()

    def abort_warmup(self, token: int):
        with self.cv:
            if self.warmup_owner == token:
                self.warmup_owner = None
                self.cv.notify_all()

    def begin_maintenance(self, token: int):
        with self.cv:
            state = self.sessions.get(token)
            if state is None or not state["warmed"]:
                raise ValueError(
                    "accelerator maintenance requires completed warm-up"
                )
            while (
                not self.stopping
                and token in self.sessions
                and not self.sessions[token]["cancelled"]
                and (
                    self.warmup_owner is not None
                    or self.maintenance_owner is not None
                    or self.executing
                )
            ):
                self.cv.wait()
            state = self.sessions.get(token)
            if self.stopping or state is None or state["cancelled"]:
                raise RuntimeError("duplex session is closed")
            self.maintenance_owner = token

    def finish_maintenance(self, token: int):
        with self.cv:
            if self.maintenance_owner == token:
                self.maintenance_owner = None
                self.cv.notify_all()

    def is_warmed(self, token: int) -> bool:
        with self.cv:
            state = self.sessions.get(token)
            return state is not None and state["warmed"]

    def run(self, token: int, callable_value, phase: str, cost: int):
        phase = str(phase)
        if not phase or len(phase.encode("utf-8")) > 128:
            raise ValueError("accelerator phase is empty or too large")
        job = {
            "token": token,
            "callable": callable_value,
            "phase": phase,
            "cost": min(1024, max(1, int(cost))),
            "submitted_ns": time.monotonic_ns(),
            "result": None,
            "error": None,
            "done": threading.Event(),
        }
        with self.cv:
            state = self.sessions.get(token)
            if state is None or state["cancelled"] or self.stopping:
                raise RuntimeError("duplex session is closed")
            if len(state["queue"]) >= int(
                state["configuration"]["maximumQueuedSteps"]
            ):
                self.rejected_steps += 1
                raise RuntimeError("MLX accelerator step queue exhausted")
            state["queue"].append(job)
            self.cv.notify_all()
        job["done"].wait()
        if job["error"] is not None:
            raise job["error"]
        return job["result"]

    def snapshot(self, token: int) -> dict:
        with self.cv:
            if token not in self.sessions:
                raise RuntimeError("duplex session is closed")
            return {
                "lane_name": self.sessions[token]["configuration"]["laneName"],
                "lane_thread_id": self.thread_id,
                "queue_depth": sum(
                    len(state["queue"]) for state in self.sessions.values()
                ),
                "active_sessions": len(self.sessions),
                "executing": self.executing,
                "phase": self.last_phase,
                "queue_wait_ns": self.last_queue_wait_ns,
                "execution_ns": self.last_execution_ns,
                "completed_steps": self.completed_steps,
                "rejected_steps": self.rejected_steps,
            }

    def shutdown(self):
        with self.cv:
            self.stopping = True
            states = list(self.sessions.values())
            self.sessions.clear()
            self.order.clear()
            self.cv.notify_all()
        for state in states:
            for job in state["queue"]:
                job["error"] = RuntimeError(
                    "MLX accelerator lane shut down"
                )
                job["done"].set()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _run_loop(self):
        with self.cv:
            self.thread_id = threading.get_ident()
            self.cv.notify_all()
        while True:
            with self.cv:
                while (
                    not self.stopping
                    and not self._has_runnable_job_locked()
                ):
                    self.cv.wait()
                if self.stopping:
                    return
                job = self._next_job_locked()
                if job is None:
                    raise RuntimeError(
                        "accelerator lane reported runnable work without a job"
                    )
                self.executing = True
                self.last_phase = job["phase"]
                self.last_queue_wait_ns = (
                    time.monotonic_ns() - job["submitted_ns"]
                )
            started = time.monotonic_ns()
            try:
                result = job["callable"]()
                with self.cv:
                    state = self.sessions.get(job["token"])
                    alive = state is not None and not state["cancelled"]
                if alive:
                    job["result"] = result
                else:
                    job["error"] = RuntimeError(
                        "cancelled MLX result was ignored at its safe point"
                    )
            except BaseException as error:
                job["error"] = error
            finally:
                with self.cv:
                    self.executing = False
                    self.last_execution_ns = time.monotonic_ns() - started
                    self.completed_steps += 1
                    self.cv.notify_all()
                job["done"].set()

    def _has_runnable_job_locked(self):
        exclusive_owner = self.warmup_owner
        if exclusive_owner is None:
            exclusive_owner = self.maintenance_owner
        if exclusive_owner is None:
            return any(
                state["queue"] for state in self.sessions.values()
            )
        state = self.sessions.get(exclusive_owner)
        return state is not None and bool(state["queue"])

    def _next_job_locked(self):
        if not self.order:
            return None
        exclusive_owner = self.warmup_owner
        if exclusive_owner is None:
            exclusive_owner = self.maintenance_owner
        maximum_passes = 1024 * max(1, len(self.order))
        for _ in range(maximum_passes):
            if self.cursor >= len(self.order):
                self.cursor = 0
            token = self.order[self.cursor]
            self.cursor = (self.cursor + 1) % len(self.order)
            if exclusive_owner is not None and token != exclusive_owner:
                continue
            state = self.sessions.get(token)
            if state is None or not state["queue"]:
                continue
            weight = int(state["configuration"]["schedulingWeight"])
            state["deficit"] = min(4096, state["deficit"] + weight)
            candidate = state["queue"][0]
            if candidate["cost"] > state["deficit"]:
                continue
            state["deficit"] -= candidate["cost"]
            return state["queue"].popleft()
        return None

class _GuestDuplexNativeBridge:
    """Object installed as the generated helper's `_native` implementation."""

    def __init__(self, manager: "_GuestDuplexSessionManager"):
        self._manager = manager

    def _session(self, token: int) -> "_GuestDuplexSession":
        return self._manager.session_for_token(int(token))

    def receive(self, token, timeout):
        return self._session(token).receive_for_python(timeout)

    def send(
        self,
        token,
        buffer,
        format_value,
        timestamp_ns,
        processed_through,
        flags,
        blocking,
    ):
        return self._session(token).send_from_python(
            buffer,
            format_value,
            timestamp_ns,
            processed_through,
            int(flags),
            bool(blocking),
        )

    def ready(self, token, metadata):
        self._session(token).ready_from_python(metadata)

    def finish_output(self, token):
        self._session(token).finish_output_from_python()

    def record_discontinuity(
        self,
        token,
        frames,
        bytes_value,
        duration_ns,
        reason,
    ):
        if int(frames) <= 0 or int(bytes_value) != 0:
            raise ValueError(
                "media v1 discontinuity requires frames > 0 and bytes == 0"
            )
        self._session(token).record_output_discontinuity(
            int(frames),
            int(duration_ns),
            reason,
        )

    def send_event(
        self,
        token,
        kind,
        payload,
        produced_through,
        processed_input_through,
    ):
        self._session(token).send_event_from_python(
            str(kind),
            payload,
            produced_through,
            processed_input_through,
        )

    def interruption_completed(self, token, interruption_id, disposition):
        self._session(token).interruption_completed_from_python(
            str(interruption_id),
            str(disposition),
        )

    def cancel_reason(self, token):
        return self._session(token).cancel_reason()

    def interruption_generation(self, token):
        return self._session(token).interruption_generation()

    def latest_interruption(self, token):
        return self._session(token).latest_interruption()

    def configuration(self, token):
        return self._session(token).python_configuration()

    def handler_thread_id(self, token):
        return self._session(token).handler_thread_id()

    def accelerator_run(self, token, callable_value, phase, estimated_cost):
        return self._manager.accelerator_lane.run(
            int(token),
            callable_value,
            str(phase),
            int(estimated_cost),
        )

    def accelerator_begin_warmup(self, token):
        self._manager.accelerator_lane.begin_warmup(int(token))

    def accelerator_finish_warmup(self, token, representative_shapes):
        self._manager.accelerator_lane.finish_warmup(
            int(token),
            int(representative_shapes),
        )

    def accelerator_abort_warmup(self, token):
        self._manager.accelerator_lane.abort_warmup(int(token))

    def accelerator_begin_maintenance(self, token):
        self._manager.accelerator_lane.begin_maintenance(int(token))

    def accelerator_finish_maintenance(self, token):
        self._manager.accelerator_lane.finish_maintenance(int(token))

    def accelerator_snapshot(self, token):
        return self._manager.accelerator_lane.snapshot(int(token))

class _GuestDuplexSessionManager:
    def __init__(self, worker: "Worker", transport_mode: str):
        if transport_mode not in ("uds", "vsock"):
            raise ValueError(f"invalid duplex transport mode: {transport_mode}")
        self.worker = worker
        self.transport_mode = transport_mode
        self.transport_name = (
            "duplex.uds.v1"
            if transport_mode == "uds"
            else "duplex.vsock.v1"
        )
        self._lock = threading.Lock()
        self._sessions: dict[str, _GuestDuplexSession] = {}
        self._tokens: dict[int, _GuestDuplexSession] = {}
        self._next_token = 1
        self.accelerator_lane = _GuestAcceleratorLane()
        self.native_bridge = _GuestDuplexNativeBridge(self)

    def capability_declaration(self) -> dict:
        return capability_declaration(
            CURRENT_PROTOCOL_VERSION,
            self.transport_name,
        )

    def session_for_token(self, token: int) -> "_GuestDuplexSession":
        with self._lock:
            session = self._tokens.get(token)
        if session is None:
            raise RuntimeError("duplex session is closed")
        return session

    def session(self, session_id: str) -> "_GuestDuplexSession":
        key = str(uuid.UUID(str(session_id))).upper()
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            raise _GuestDuplexError("unknown duplex session")
        return session

    def open(self, command: dict) -> tuple[dict, "_GuestDuplexSession"]:
        session_id = str(uuid.UUID(str(command["sessionID"]))).upper()
        generation = int(command["expectedGeneration"])
        channel_id = int(command["controlChannelID"])
        endpoint_descriptor = command["mediaEndpoint"]
        configuration = command["configuration"]
        authentication = command["authentication"]

        self._validate_open(
            session_id,
            generation,
            channel_id,
            configuration,
            endpoint_descriptor,
            authentication,
        )
        with self._lock:
            if session_id in self._sessions:
                raise _GuestDuplexError("duplicate guest duplex session")
            if len(self._sessions) >= DUPLEX_MAXIMUM_ACTIVE_SESSIONS:
                raise _GuestDuplexResourceError(
                    "guest duplex session admission exhausted"
                )
            token = self._next_token
            self._next_token += 1

        media_socket = self._open_media_socket(endpoint_descriptor["endpoint"])
        owns_socket = True
        accelerator_registered = False
        try:
            nonce = base64.b64decode(authentication["nonce"], validate=True)
            secret = base64.b64decode(authentication["secret"], validate=True)
            digest = base64.b64decode(
                authentication["configurationDigest"],
                validate=True,
            )
            identity = {
                "session_id": session_id,
                "worker_id": self.worker.worker_id,
                "generation": generation,
                "nonce_id": endpoint_descriptor["nonceID"],
                "nonce": nonce,
                "configuration_digest": digest,
                "configuration": configuration,
            }
            host_identity = dict(identity)
            host_identity.update({
                "sender_role": DUPLEX_MEDIA_ROLES["host"],
                "receiver_role": DUPLEX_MEDIA_ROLES["worker"],
            })
            _duplex_authenticate_handshake(
                recv_exact(media_socket, 180, timeout=2),
                secret,
                **host_identity,
            )
            worker_identity = dict(identity)
            worker_identity.update({
                "sender_role": DUPLEX_MEDIA_ROLES["worker"],
                "receiver_role": DUPLEX_MEDIA_ROLES["host"],
            })
            send_all(
                media_socket,
                _duplex_encode_handshake(secret, **worker_identity),
                timeout=2,
            )

            session = _GuestDuplexSession(
                manager=self,
                worker=self.worker,
                token=token,
                session_id=session_id,
                generation=generation,
                control_channel_id=channel_id,
                media_socket=media_socket,
                configuration=configuration,
                handler=command["handler"],
                arguments=command.get("arguments", []),
                kwargs=command.get("kwargs", {}),
            )
            self.accelerator_lane.register(token, configuration)
            accelerator_registered = True
            with self._lock:
                if session_id in self._sessions:
                    raise _GuestDuplexError(
                        "duplicate guest duplex session appeared during open"
                    )
                if len(self._sessions) >= DUPLEX_MAXIMUM_ACTIVE_SESSIONS:
                    raise _GuestDuplexResourceError(
                        "guest duplex session admission exhausted during open"
                    )
                self._sessions[session_id] = session
                self._tokens[token] = session
            owns_socket = False
            return (
                {
                    "sessionID": session_id,
                    "controlChannelID": channel_id,
                    "generation": generation,
                    "negotiated": {
                        "transport": self.transport_name,
                        "configuration": configuration,
                    },
                },
                session,
            )
        except TimeoutError as error:
            raise _GuestDuplexError("media handshake timed out") from error
        finally:
            if owns_socket:
                if accelerator_registered:
                    self.accelerator_lane.unregister(token)
                try:
                    media_socket.close()
                except Exception:
                    pass

    def _validate_open(
        self,
        session_id: str,
        generation: int,
        channel_id: int,
        configuration: dict,
        endpoint_descriptor: dict,
        authentication: dict,
    ):
        if generation <= 0 or channel_id <= 0:
            raise _GuestDuplexError("invalid duplex generation or route")
        max_frame_bytes = int(configuration.get("maxFrameBytes", 0))
        max_logical_message_bytes = int(
            configuration.get("maxLogicalMessageBytes", max_frame_bytes)
        )
        preferred_message_chunk_bytes = int(
            configuration.get(
                "preferredMessageChunkBytes",
                min(max_frame_bytes, 256 * 1024),
            )
        )
        max_outstanding_messages = int(
            configuration.get(
                "maxOutstandingMessages",
                1 if max_frame_bytes > 0 else 0,
            )
        )
        media_protocol_version = int(configuration["mediaProtocolVersion"])
        endpoint_media_protocol_version = int(
            endpoint_descriptor["mediaProtocolVersion"]
        )
        if (
            str(uuid.UUID(str(endpoint_descriptor["sessionID"]))).upper()
            != session_id
            or int(endpoint_descriptor["expectedWorkerID"])
            != self.worker.worker_id
            or int(endpoint_descriptor["expectedGeneration"]) != generation
            or endpoint_media_protocol_version != media_protocol_version
            or media_protocol_version != DUPLEX_MEDIA_PROTOCOL_VERSION
        ):
            raise _GuestDuplexError("duplex endpoint reservation mismatch")
        expected_role = (
            DUPLEX_MEDIA_ROLES["host"]
            if self.transport_mode == "uds"
            else DUPLEX_MEDIA_ROLES["worker"]
        )
        if int(endpoint_descriptor["listenerRole"]) != expected_role:
            raise _GuestDuplexError("duplex listener role mismatch")
        endpoint = endpoint_descriptor["endpoint"]
        expected_case = "uds" if self.transport_mode == "uds" else "vsock"
        if set(endpoint) != {expected_case}:
            raise _GuestDuplexError("duplex endpoint transport mismatch")

        format_descriptors = configuration.get("formats", [])
        format_ids = [int(item["id"]) for item in format_descriptors]
        formats_are_bounded = (
            len(format_descriptors) <= DUPLEX_MAXIMUM_FORMATS
            and all(
                item.get("kind")
                and len(str(item["kind"]).encode("utf-8"))
                    <= DUPLEX_MAXIMUM_DESCRIPTOR_STRING_BYTES
                and len(item.get("metadata", {}))
                    <= DUPLEX_MAXIMUM_FORMAT_METADATA_ENTRIES
                and all(
                    key
                    and len(str(key).encode("utf-8"))
                        <= DUPLEX_MAXIMUM_DESCRIPTOR_STRING_BYTES
                    and len(str(value).encode("utf-8"))
                        <= DUPLEX_MAXIMUM_DESCRIPTOR_STRING_BYTES
                    for key, value in item.get("metadata", {}).items()
                )
                for item in format_descriptors
            )
        )
        maximum_transport_frame_bytes = (
            DUPLEX_MAXIMUM_VSOCK_MEDIA_FRAME_BYTES
            if self.transport_mode == "vsock"
            else DUPLEX_MAXIMUM_MEDIA_FRAME_BYTES
        )
        if (
            media_protocol_version != DUPLEX_MEDIA_PROTOCOL_VERSION
            or configuration["authenticationMode"] != "hmac-sha256"
            or configuration["checksumMode"] != "none"
            or not format_descriptors
            or not formats_are_bounded
            or len(format_ids) != len(set(format_ids))
            or int(configuration["ingressCreditFrames"]) <= 0
            or int(configuration["egressCreditFrames"]) <= 0
            or int(configuration["ingressCreditFrames"])
                > DUPLEX_MAXIMUM_CREDIT_FRAMES
            or int(configuration["egressCreditFrames"])
                > DUPLEX_MAXIMUM_CREDIT_FRAMES
            or int(configuration["ingressCreditBytes"]) <= 0
            or int(configuration["egressCreditBytes"]) <= 0
            or max_frame_bytes <= 0
            or max_frame_bytes > maximum_transport_frame_bytes
            or max_logical_message_bytes <= 0
            or max_logical_message_bytes > DUPLEX_MAXIMUM_LOGICAL_MESSAGE_BYTES
            or preferred_message_chunk_bytes <= 0
            or preferred_message_chunk_bytes > max_frame_bytes
            or max_outstanding_messages <= 0
            or max_outstanding_messages > DUPLEX_MAXIMUM_OUTSTANDING_MESSAGES
            or int(configuration["ingressCreditBytes"])
                > DUPLEX_MAXIMUM_INGRESS_CREDIT_BYTES
            or int(configuration["egressCreditBytes"])
                > DUPLEX_MAXIMUM_EGRESS_CREDIT_BYTES
            or configuration.get("arenaPool") is not None
        ):
            raise _GuestDuplexError(
                "duplex configuration is outside guest capability"
            )
        accelerator = configuration.get("accelerator", {"kind": "none"})
        if accelerator.get("kind") not in {"none", "mlx"}:
            raise _GuestDuplexError("unsupported duplex accelerator kind")
        if accelerator.get("kind") == "none":
            zero_fields = (
                "maximumQueuedSteps",
                "maximumActiveSessions",
                "maximumResidentModels",
                "maximumResidentBytes",
                "maximumSimultaneousLeases",
                "defaultModelTTLMilliseconds",
                "cacheClearMinimumIntervalMilliseconds",
                "warmupTimeoutMilliseconds",
                "maximumProcessLanes",
                "schedulingWeight",
                "maximumStateItems",
                "maximumStateBytes",
                "softPressureRatioPermille",
                "throttlePressureRatioPermille",
                "shedPressureRatioPermille",
            )
            if (
                accelerator.get("laneName") != ""
                or accelerator.get("startupStressProbe") is not None
                or any(int(accelerator.get(field, -1)) != 0
                       for field in zero_fields)
            ):
                raise _GuestDuplexError(
                    "guest none accelerator configuration is not canonical"
                )
        else:
            stress_probe = accelerator.get("startupStressProbe")
            if (
                not accelerator.get("laneName")
                or len(accelerator["laneName"].encode("utf-8"))
                    > DUPLEX_MAXIMUM_DESCRIPTOR_STRING_BYTES
                or int(accelerator["maximumQueuedSteps"]) <= 0
                or int(accelerator["maximumQueuedSteps"])
                    > DUPLEX_MAXIMUM_ACCELERATOR_QUEUED_STEPS
                or int(accelerator["maximumActiveSessions"]) <= 0
                or int(accelerator["maximumActiveSessions"])
                    > DUPLEX_MAXIMUM_ACTIVE_SESSIONS
                or int(accelerator["maximumResidentModels"]) <= 0
                or int(accelerator["maximumResidentModels"])
                    > DUPLEX_MAXIMUM_ACCELERATOR_RESIDENT_MODELS
                or int(accelerator["maximumResidentBytes"]) <= 0
                or int(accelerator["maximumResidentBytes"])
                    > DUPLEX_MAXIMUM_ACCELERATOR_RESIDENT_BYTES
                or int(accelerator["maximumSimultaneousLeases"]) <= 0
                or int(accelerator["maximumSimultaneousLeases"])
                    > DUPLEX_MAXIMUM_ACCELERATOR_LEASES
                or int(accelerator["defaultModelTTLMilliseconds"]) <= 0
                or int(
                    accelerator["cacheClearMinimumIntervalMilliseconds"]
                ) <= 0
                or int(accelerator["warmupTimeoutMilliseconds"]) <= 0
                or int(accelerator["maximumProcessLanes"]) <= 0
                or int(accelerator["maximumProcessLanes"])
                    > DUPLEX_MAXIMUM_ACCELERATOR_PROCESS_LANES
                or (
                    stress_probe is not None
                    and (
                        not stress_probe
                        or len(stress_probe.encode("utf-8"))
                            > DUPLEX_MAXIMUM_DESCRIPTOR_STRING_BYTES
                    )
                )
                or int(accelerator["schedulingWeight"]) <= 0
                or int(accelerator["schedulingWeight"])
                    > DUPLEX_MAXIMUM_ACCELERATOR_SCHEDULING_WEIGHT
                or int(accelerator["maximumStateItems"]) <= 0
                or int(accelerator["maximumStateItems"])
                    > DUPLEX_MAXIMUM_ACCELERATOR_STATE_ITEMS
                or int(accelerator["maximumStateBytes"]) <= 0
                or int(accelerator["maximumStateBytes"])
                    > DUPLEX_MAXIMUM_ACCELERATOR_STATE_BYTES
                or int(accelerator["softPressureRatioPermille"]) <= 0
                or int(accelerator["softPressureRatioPermille"])
                    > int(accelerator["throttlePressureRatioPermille"])
                or int(accelerator["throttlePressureRatioPermille"])
                    > int(accelerator["shedPressureRatioPermille"])
                or int(accelerator["shedPressureRatioPermille"]) > 1000
                or (
                    int(accelerator["maximumProcessLanes"]) > 1
                    and stress_probe is None
                )
            ):
                raise _GuestDuplexError(
                    "guest MLX accelerator configuration is invalid"
                )
        if authentication.get("mode") != "hmac-sha256":
            raise _GuestDuplexError("duplex authentication mode mismatch")
        for field, expected_length in (
            ("nonce", 32),
            ("secret", 32),
            ("configurationDigest", 32),
        ):
            try:
                value = base64.b64decode(authentication[field], validate=True)
            except Exception as error:
                raise _GuestDuplexError(
                    f"duplex authentication {field} is not base64"
                ) from error
            if len(value) != expected_length:
                raise _GuestDuplexError(
                    f"duplex authentication {field} length mismatch"
                )

        # Swift hashes JSONEncoder output with sorted keys. Separators match
        # Foundation's compact representation; enum raw values are already
        # strings in this value tree.
        canonical_text = json.dumps(
            configuration,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).replace("/", "\\/")
        canonical = canonical_text.encode("utf-8")
        supplied_digest = base64.b64decode(
            authentication["configurationDigest"],
            validate=True,
        )
        if not hmac.compare_digest(hashlib.sha256(canonical).digest(), supplied_digest):
            raise _GuestDuplexError("duplex configuration digest mismatch")
        # Older v6 hosts omit the additive message fields. Populate their v1
        # defaults only after authenticating the exact transmitted JSON.
        configuration.setdefault(
            "maxLogicalMessageBytes",
            max_logical_message_bytes,
        )
        configuration.setdefault(
            "preferredMessageChunkBytes",
            preferred_message_chunk_bytes,
        )
        configuration.setdefault(
            "maxOutstandingMessages",
            max_outstanding_messages,
        )
        configuration.setdefault("arenaPool", None)

    def _open_media_socket(self, endpoint: dict) -> socket.socket:
        if self.transport_mode == "uds":
            path = endpoint["uds"]["path"]
            if not isinstance(path, str) or not path:
                raise _GuestDuplexError("duplex UDS path is empty")
            media_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                media_socket.settimeout(2)
                media_socket.connect(path)
                media_socket.settimeout(None)
                return media_socket
            except BaseException:
                media_socket.close()
                raise
        port = int(endpoint["vsock"]["port"])
        if port < 1024 or port > 0xFFFFFFFF:
            raise _GuestDuplexError("duplex vsock port is outside bounds")
        return listen_vsock(port, timeout=10)

    def remove(self, session: "_GuestDuplexSession"):
        self.accelerator_lane.unregister(session.token)
        with self._lock:
            if self._sessions.get(session.session_id) is session:
                self._sessions.pop(session.session_id, None)
            if self._tokens.get(session.token) is session:
                self._tokens.pop(session.token, None)

    def cancel_callback_waiters(self, channel_id: int, reason: str):
        self.worker._fail_callback_waiters_for_channel(
            channel_id,
            RuntimeError(f"duplex session cancelled: {reason}"),
        )

    def shutdown_all(self):
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.cancel((1 << 64) - 1, "shutdown")
        for session in sessions:
            session.wait_for_cleanup(2)
        self.accelerator_lane.shutdown()

class _GuestIngressCompletionLedger:
    """Bounded contiguous watermark plus coalesced completed ranges."""

    def __init__(self):
        self.completed_through = 0
        self.pending: list[tuple[int, int]] = []

    def record(
        self,
        first: int,
        last: int,
        maximum_pending_ranges: int,
    ) -> bool:
        maximum_sequence = (1 << 64) - 1
        if (
            first <= 0
            or first > last
            or last > maximum_sequence
            or maximum_pending_ranges <= 0
        ):
            return False
        if last <= self.completed_through:
            return True
        lower = max(first, self.completed_through + 1)
        merged_first = lower
        merged_last = last
        start = 0
        while (
            start < len(self.pending)
            and self.pending[start][1] + 1 < merged_first
        ):
            start += 1
        end = start
        while (
            end < len(self.pending)
            and merged_last + 1 >= self.pending[end][0]
        ):
            merged_first = min(merged_first, self.pending[end][0])
            merged_last = max(merged_last, self.pending[end][1])
            end += 1
        resulting_count = len(self.pending) - (end - start) + 1
        if resulting_count > maximum_pending_ranges:
            return False
        self.pending[start:end] = [(merged_first, merged_last)]
        self._advance_prefix()
        return True

    def declare_completed(self, through: int):
        if through > self.completed_through:
            self.completed_through = through
        self._advance_prefix()

    def _advance_prefix(self):
        maximum_sequence = (1 << 64) - 1
        while self.pending:
            first, last = self.pending[0]
            if last <= self.completed_through:
                self.pending.pop(0)
                continue
            if self.completed_through == maximum_sequence:
                self.pending.clear()
                return
            if first > self.completed_through + 1:
                return
            self.completed_through = last
            self.pending.pop(0)

class _GuestDuplexSession:
    def __init__(
        self,
        *,
        manager: _GuestDuplexSessionManager,
        worker: "Worker",
        token: int,
        session_id: str,
        generation: int,
        control_channel_id: int,
        media_socket: socket.socket,
        configuration: dict,
        handler: dict,
        arguments: list,
        kwargs: dict,
    ):
        self.manager = manager
        self.worker = worker
        self.token = token
        self.session_id = session_id
        self.generation = generation
        self.control_channel_id = control_channel_id
        self.sock = media_socket
        self.configuration = configuration
        self.handler = handler
        self.arguments = arguments
        self.kwargs = kwargs

        self.cv = threading.Condition()
        self.application_controls = collections.deque()
        self.interruptions = collections.deque()
        self.ingress = collections.deque()
        self.ingress_messages = {}
        self.ingress_bytes = 0
        self.retained_ingress_frames = 0
        self.retained_ingress_bytes = 0
        self.expected_ingress_sequence = 1
        self.input_accepted_through = 0
        self.processed_ingress_completion = _GuestIngressCompletionLedger()
        self.released_ingress_completion = _GuestIngressCompletionLedger()
        self.returned_ingress_frames = 0
        self.returned_ingress_bytes = 0

        self.available_egress_frames = int(
            configuration["egressCreditFrames"]
        )
        self.available_egress_bytes = int(
            configuration["egressCreditBytes"]
        )
        self.released_egress_frames = 0
        self.released_egress_bytes = 0
        self.released_egress_through = 0
        self.output_produced_through = 0
        self.output_produced_bytes = 0
        self.output_acknowledged_through = None

        self.last_control_sequence = 0
        self.interruption_count = 0
        self.latest_interruption_value = None
        self.pending_interruption_ids: set[str] = set()
        self.cancellation_reason = None
        self.input_finished = False
        self.output_finished = False
        self.ready_sent = False
        self.handler_returned = False
        self.handler_ident = 0
        self.terminal = None
        self.cleanup_started = False
        self.cleanup_done = threading.Event()

        # Data frames are already bounded by negotiated egress credit. Four
        # fixed structural slots prevent credit from starving close/pong.
        queue_capacity = int(configuration["egressCreditFrames"]) + 4
        self.writer_queue: queue.Queue = queue.Queue(maxsize=queue_capacity)
        self.writer_thread = None
        self.reader_thread = None
        self.handler_thread = None

    @property
    def input_processed_through(self) -> int:
        return self.processed_ingress_completion.completed_through

    @property
    def released_ingress_through(self) -> int:
        return self.released_ingress_completion.completed_through

    def start(self):
        self.writer_thread = threading.Thread(
            target=self._writer_loop,
            name=f"swiftpython-vm-duplex-writer-{self.token}",
            daemon=True,
        )
        self.reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"swiftpython-vm-duplex-reader-{self.token}",
            daemon=True,
        )
        self.handler_thread = threading.Thread(
            target=self._handler_loop,
            name=f"swiftpython-vm-duplex-handler-{self.token}",
            daemon=True,
        )
        self.writer_thread.start()
        self.reader_thread.start()
        self.handler_thread.start()

    def _writer_loop(self):
        try:
            while True:
                item = self.writer_queue.get()
                if item is None:
                    return
                header, payload_view = item
                if payload_view is not None:
                    send_all(self.sock, header, payload_view)
                else:
                    send_all(self.sock, header)
                if payload_view is not None:
                    payload_view.release()
        except BaseException as error:
            if not self._is_terminal():
                self.fail("mediaProtocol", f"guest media writer failed: {error}")

    def _reader_loop(self):
        try:
            while not self._is_terminal():
                envelope = _duplex_read_envelope(
                    self.sock,
                    int(self.configuration["maxFrameBytes"]),
                    int(self.configuration["mediaProtocolVersion"]),
                )
                self._handle_media(envelope)
        except (ConnectionError, ConnectionResetError, BrokenPipeError) as error:
            if not self._is_terminal():
                self.fail("mediaEOF", f"guest media transport closed: {error}")
        except BaseException as error:
            if not self._is_terminal():
                self.fail("mediaProtocol", f"guest media protocol failed: {error}")

    def _handle_media(self, envelope: dict):
        kind = envelope["kind"]
        direction = envelope["direction"]
        if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["data"]:
            if direction != DUPLEX_MEDIA_DIRECTIONS["ingress"]:
                raise _GuestDuplexError("guest received data for wrong direction")
            sequence = envelope["sequence"]
            payload = envelope["payload"]
            format_id = envelope["format_id"]
            valid_formats = {
                int(item["id"]) for item in self.configuration["formats"]
            }
            with self.cv:
                if (
                    self.terminal is not None
                    or self.input_finished
                    or sequence != self.expected_ingress_sequence
                    or envelope["processed_through"] is not None
                    or format_id not in valid_formats
                    or self.ingress_bytes + len(payload)
                        > int(self.configuration["ingressCreditBytes"])
                    or len(self.ingress) + self.retained_ingress_frames
                        >= int(self.configuration["ingressCreditFrames"])
                ):
                    raise _GuestDuplexError(
                        "guest ingress sequence, format, or credit bound violated"
                    )
                release = lambda: self._release_python_input(
                    sequence,
                    len(payload),
                )
                exporter = _CreditByteArray(payload, release)
                self.ingress.append({
                    "kind": "input",
                    "sequence": sequence,
                    "timestamp_ns": envelope["timestamp_ns"],
                    "format_id": format_id,
                    "flags": envelope["flags"],
                    "exporter": exporter,
                })
                self.ingress_bytes += len(payload)
                self.expected_ingress_sequence = sequence + 1
                self.input_accepted_through = sequence
                # The media and handler threads share the main response
                # channel. Emit acceptance before waking the handler so a
                # processed event for this frame cannot overtake it.
                self._send_event(
                    0,
                    {"inputAccepted": {"through": sequence}},
                )
                self.cv.notify_all()
            return

        if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["messageChunk"]:
            if (
                direction != DUPLEX_MEDIA_DIRECTIONS["ingress"]
                or envelope["processed_through"] is not None
            ):
                raise _GuestDuplexError(
                    "guest received an invalid logical-message chunk"
                )
            sequence = envelope["sequence"]
            payload = envelope["payload"]
            message_id = envelope["message_id"]
            total_bytes = envelope["total_bytes"]
            byte_offset = envelope["byte_offset"]
            chunk_index = envelope["chunk_index"]
            chunk_count = envelope["chunk_count"]
            format_id = envelope["format_id"]
            valid_formats = {
                int(item["id"]) for item in self.configuration["formats"]
            }
            with self.cv:
                state = self.ingress_messages.get(message_id)
                if state is None:
                    if (
                        byte_offset != 0
                        or chunk_index != 0
                        or len(self.ingress_messages)
                            >= int(self.configuration["maxOutstandingMessages"])
                    ):
                        raise _GuestDuplexError(
                            "logical message did not start at chunk zero or admission is exhausted"
                        )
                    state = {
                        "total_bytes": total_bytes,
                        "timestamp_ns": envelope["timestamp_ns"],
                        "format_id": format_id,
                        "flags": envelope["flags"],
                        "chunk_count": chunk_count,
                        "next_offset": 0,
                        "next_chunk_index": 0,
                    }
                if (
                    state["total_bytes"] != total_bytes
                    or state["timestamp_ns"] != envelope["timestamp_ns"]
                    or state["format_id"] != format_id
                    or state["flags"] != envelope["flags"]
                    or state["chunk_count"] != chunk_count
                    or state["next_offset"] != byte_offset
                    or state["next_chunk_index"] != chunk_index
                    or self.terminal is not None
                    or self.input_finished
                    or sequence != self.expected_ingress_sequence
                    or total_bytes
                        > int(self.configuration["maxLogicalMessageBytes"])
                    or format_id not in valid_formats
                    or self.ingress_bytes + len(payload)
                        > int(self.configuration["ingressCreditBytes"])
                    or len(self.ingress) + self.retained_ingress_frames
                        >= int(self.configuration["ingressCreditFrames"])
                ):
                    raise _GuestDuplexError(
                        "logical-message order, identity, format, or credit bound violated"
                    )
                end = byte_offset + len(payload)
                next_index = chunk_index + 1
                if (
                    end > total_bytes
                    or next_index > (1 << 32) - 1
                    or (
                        chunk_count is not None
                        and (
                            next_index > chunk_count
                            or (
                                end == total_bytes
                                and next_index != chunk_count
                            )
                            or (
                                end != total_bytes
                                and next_index >= chunk_count
                            )
                        )
                    )
                ):
                    raise _GuestDuplexError(
                        "logical-message coverage is invalid"
                    )
                release = lambda: self._release_python_input(
                    sequence,
                    len(payload),
                )
                exporter = _CreditByteArray(payload, release)
                self.ingress.append({
                    "kind": "message_chunk",
                    "sequence": sequence,
                    "timestamp_ns": envelope["timestamp_ns"],
                    "message_id": message_id,
                    "total_bytes": total_bytes,
                    "byte_offset": byte_offset,
                    "chunk_index": chunk_index,
                    "chunk_count": chunk_count,
                    "format_id": format_id,
                    "flags": envelope["flags"],
                    "exporter": exporter,
                })
                self.ingress_bytes += len(payload)
                if end == total_bytes:
                    self.ingress_messages.pop(message_id, None)
                else:
                    state["next_offset"] = end
                    state["next_chunk_index"] = next_index
                    self.ingress_messages[message_id] = state
                self.expected_ingress_sequence = sequence + 1
                self.input_accepted_through = sequence
                self._send_event(
                    0,
                    {"inputAccepted": {"through": sequence}},
                )
                self.cv.notify_all()
            return

        if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["messageAbort"]:
            if (
                direction != DUPLEX_MEDIA_DIRECTIONS["ingress"]
            ):
                raise _GuestDuplexError(
                    "guest received an invalid logical-message abort"
                )
            with self.cv:
                message_id = envelope["message_id"]
                if (
                    self.terminal is not None
                    or self.input_finished
                    or self.ingress_messages.pop(message_id, None) is None
                ):
                    raise _GuestDuplexError(
                        "logical-message abort did not name an active message"
                    )
                self.ingress.append({
                    "kind": "message_aborted",
                    "message_id": message_id,
                    "reason_code": envelope["reason_code"],
                })
                self.cv.notify_all()
            return

        if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["credit"]:
            if direction != DUPLEX_MEDIA_DIRECTIONS["egress"]:
                raise _GuestDuplexError("guest received credit for wrong direction")
            with self.cv:
                frames = envelope["released_frames"]
                bytes_value = envelope["released_bytes"]
                through = envelope["released_through"]
                frame_delta = frames - self.released_egress_frames
                byte_delta = bytes_value - self.released_egress_bytes
                if (
                    self.terminal is not None
                    or frame_delta < 0
                    or byte_delta < 0
                    or through < self.released_egress_through
                    or through > self.output_produced_through
                    or frame_delta
                        > int(self.configuration["egressCreditFrames"])
                            - self.available_egress_frames
                    or byte_delta
                        > int(self.configuration["egressCreditBytes"])
                            - self.available_egress_bytes
                ):
                    raise _GuestDuplexError(
                        "guest egress credit regressed or over-granted"
                    )
                self.released_egress_frames = frames
                self.released_egress_bytes = bytes_value
                self.released_egress_through = through
                self.available_egress_frames += frame_delta
                self.available_egress_bytes += byte_delta
                self.cv.notify_all()
            return

        if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["directionEnd"]:
            if direction != DUPLEX_MEDIA_DIRECTIONS["ingress"]:
                raise _GuestDuplexError(
                    "guest received direction-end for wrong direction"
                )
            with self.cv:
                if (
                    self.terminal is not None
                    or self.input_finished
                    or self.ingress_messages
                    or envelope["sequence"] != self.input_accepted_through
                ):
                    raise _GuestDuplexError(
                        "guest ingress direction-end watermark mismatch"
                    )
                self.input_finished = True
                should_complete = (
                    self.handler_returned
                    and self.cancellation_reason is None
                )
                self.cv.notify_all()
            if should_complete:
                self.finish({"completed": {}}, drain_writer=True)
            return

        if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["discontinuity"]:
            if direction != DUPLEX_MEDIA_DIRECTIONS["ingress"]:
                raise _GuestDuplexError(
                    "guest received discontinuity for wrong direction"
                )
            first = envelope["sequence"]
            last = envelope["last_sequence"]
            with self.cv:
                if (
                    self.terminal is not None
                    or self.input_finished
                    or first != self.expected_ingress_sequence
                    or len(self.ingress) + self.retained_ingress_frames
                        >= int(self.configuration["ingressCreditFrames"])
                ):
                    raise _GuestDuplexError(
                        "guest ingress discontinuity sequence mismatch"
                    )
                previous_processed = self.input_processed_through
                maximum_pending_ranges = (
                    int(self.configuration["ingressCreditFrames"]) + 1
                )
                if (
                    self.returned_ingress_frames >= (1 << 64) - 1
                    or not self.processed_ingress_completion.record(
                        first,
                        last,
                        maximum_pending_ranges,
                    )
                    or not self.released_ingress_completion.record(
                        first,
                        last,
                        maximum_pending_ranges,
                    )
                ):
                    raise _GuestDuplexError(
                        "guest ingress completion ledger exhausted"
                    )
                self.expected_ingress_sequence = last + 1
                self.input_accepted_through = last
                self.returned_ingress_frames += 1
                processed = self.input_processed_through
                self._publish_ingress_credit()
                self._send_event(0, {"inputAccepted": {"through": last}})
                if processed > previous_processed:
                    self._send_event(
                        0,
                        {"inputProcessed": {"through": processed}},
                    )
            return

        if kind == DUPLEX_MEDIA_ENVELOPE_KINDS["ping"]:
            if direction != DUPLEX_MEDIA_DIRECTIONS["ingress"]:
                raise _GuestDuplexError("guest received ping for wrong direction")
            self._enqueue_structural(
                kind=DUPLEX_MEDIA_ENVELOPE_KINDS["pong"],
                direction=DUPLEX_MEDIA_DIRECTIONS["egress"],
                timestamp_ns=envelope["timestamp_ns"],
            )
            return

        # Pongs are structural and carry no application semantics.

    def _handler_loop(self):
        self.worker.active_command_channel.channel_id = self.control_channel_id
        try:
            with self.cv:
                self.handler_ident = threading.get_ident()
                self.cv.notify_all()
            python_session = _duplex_helper.DuplexSession(self.token)
            callable_value = self._resolve_handler()
            resolved_arguments = [
                self.worker._resolve_value_descriptor(value)
                for value in self.arguments
            ]
            resolved_kwargs = {
                key: self.worker._resolve_value_descriptor(value)
                for key, value in self.kwargs.items()
            }
            callable_value(
                python_session,
                *resolved_arguments,
                **resolved_kwargs,
            )
        except BaseException as error:
            reason = self.cancel_reason()
            if reason is not None:
                self.finish(
                    {"cancelled": {"reason": reason}},
                    drain_writer=False,
                )
            else:
                with self.cv:
                    ready = self.ready_sent
                phase = "handlerRuntime" if ready else "handlerSetup"
                self.fail(
                    phase,
                    "".join(traceback.format_exception(error)).strip(),
                )
            return
        finally:
            if (
                getattr(
                    self.worker.active_command_channel,
                    "channel_id",
                    None,
                )
                == self.control_channel_id
            ):
                self.worker.active_command_channel.channel_id = 0

        try:
            self.finish_output_from_python()
        except BaseException as error:
            if self.cancel_reason() is None:
                self.fail(
                    "handlerRuntime",
                    f"implicit output finish failed: {error}",
                )
                return

        with self.cv:
            self.handler_returned = True
            cancellation = self.cancellation_reason
            should_complete = self.input_finished and cancellation is None
            returned_before_ready = not self.ready_sent
            self.cv.notify_all()
        if returned_before_ready:
            self._send_application_event("handlerReturnedBeforeReady")
        if cancellation is not None:
            self.finish(
                {"cancelled": {"reason": cancellation}},
                drain_writer=False,
            )
        elif should_complete:
            self.finish({"completed": {}}, drain_writer=True)

    def _resolve_handler(self):
        if "function" in self.handler:
            descriptor = self.handler["function"]
            module = importlib.import_module(descriptor["module"])
            return getattr(module, descriptor["name"])
        if "method" in self.handler:
            descriptor = self.handler["method"]
            target = self.worker._resolve_handle(descriptor["target"])
            return getattr(target, descriptor["name"])
        raise _GuestDuplexError("unknown duplex handler descriptor")

    def receive_for_python(self, timeout):
        if timeout is None:
            deadline = None
        else:
            timeout = float(timeout)
            if not math.isfinite(timeout) or timeout < 0:
                raise ValueError(
                    "receive timeout must be finite and non-negative"
                )
            deadline = time.monotonic() + timeout
        with self.cv:
            while (
                not self.interruptions
                and not self.application_controls
                and not self.ingress
                and not self.input_finished
                and self.cancellation_reason is None
                and self.terminal is None
            ):
                if deadline is None:
                    self.cv.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("duplex receive timed out")
                    self.cv.wait(remaining)
            if self.cancellation_reason is not None:
                return {
                    "kind": "cancelled",
                    "reason": self.cancellation_reason,
                }
            if self.terminal is not None:
                raise RuntimeError("duplex session is closed")
            if self.interruptions:
                return self.interruptions.popleft()
            if self.application_controls:
                return self.application_controls.popleft()
            if self.ingress:
                frame = self.ingress.popleft()
                if frame["kind"] == "message_aborted":
                    return frame
                exporter = frame["exporter"]
                self.retained_ingress_frames += 1
                self.retained_ingress_bytes += len(exporter)
                format_descriptor = next(
                    (
                        item
                        for item in self.configuration["formats"]
                        if int(item["id"]) == frame["format_id"]
                    ),
                    {"kind": "unknown", "metadata": {}},
                )
                result = {
                    "kind": frame["kind"],
                    "sequence": frame["sequence"],
                    "timestamp_ns": frame["timestamp_ns"],
                    "format_id": frame["format_id"],
                    "format": format_descriptor["kind"],
                    "format_metadata": dict(
                        format_descriptor.get("metadata", {})
                    ),
                    "flags": frame["flags"],
                    "storage_route": "inline",
                    "buffer": memoryview(exporter),
                }
                if frame["kind"] == "message_chunk":
                    result.update({
                        "message_id": frame["message_id"],
                        "total_bytes": frame["total_bytes"],
                        "byte_offset": frame["byte_offset"],
                        "chunk_index": frame["chunk_index"],
                        "chunk_count": frame["chunk_count"],
                    })
                return result
            return {
                "kind": "input_finished",
                "final_sequence": self.input_accepted_through,
            }

    def _release_python_input(self, sequence: int, byte_count: int):
        accounting_failure = False
        with self.cv:
            if (
                self.terminal is not None
                or sequence <= 0
                or sequence > self.input_accepted_through
                or byte_count < 0
                or self.retained_ingress_frames <= 0
                or self.retained_ingress_bytes < byte_count
                or self.ingress_bytes < byte_count
            ):
                return
            maximum_sequence = (1 << 64) - 1
            maximum_pending_ranges = (
                int(self.configuration["ingressCreditFrames"]) + 1
            )
            previous_processed = self.input_processed_through
            if (
                self.returned_ingress_frames >= maximum_sequence
                or self.returned_ingress_bytes
                    > maximum_sequence - byte_count
                or not self.processed_ingress_completion.record(
                    sequence,
                    sequence,
                    maximum_pending_ranges,
                )
                or not self.released_ingress_completion.record(
                    sequence,
                    sequence,
                    maximum_pending_ranges,
                )
            ):
                accounting_failure = True
                processed = self.input_processed_through
                should_publish = False
            else:
                self.retained_ingress_frames -= 1
                self.retained_ingress_bytes -= byte_count
                self.ingress_bytes -= byte_count
                self.returned_ingress_frames += 1
                self.returned_ingress_bytes += byte_count
                processed = self.input_processed_through
                should_publish = True
                self.cv.notify_all()
            if should_publish:
                self._publish_ingress_credit()
            if should_publish and processed > previous_processed:
                self._send_event(
                    0,
                    {"inputProcessed": {"through": processed}},
                )
        if accounting_failure:
            self.fail(
                "mediaProtocol",
                "duplex input completion accounting exhausted",
            )
            return

    def _publish_ingress_credit(self):
        with self.cv:
            released_frames = self.returned_ingress_frames
            released_bytes = self.returned_ingress_bytes
            released_through = self.released_ingress_through
        self._enqueue_structural(
            kind=DUPLEX_MEDIA_ENVELOPE_KINDS["credit"],
            direction=DUPLEX_MEDIA_DIRECTIONS["ingress"],
            sequence=released_through,
            released_frames=released_frames,
            released_bytes=released_bytes,
        )

    def _resolve_format_id(self, format_value) -> int:
        if format_value is None:
            return int(self.configuration["formats"][0]["id"])
        if isinstance(format_value, int):
            value = int(format_value)
            if any(
                int(item["id"]) == value
                for item in self.configuration["formats"]
            ):
                return value
            raise ValueError("unknown output format id")
        name = str(format_value)
        for descriptor in self.configuration["formats"]:
            if descriptor["kind"] == name:
                return int(descriptor["id"])
        raise ValueError(f"unknown output format kind {name}")

    def send_from_python(
        self,
        buffer,
        format_value,
        timestamp_ns,
        processed_input_through,
        flags: int,
        blocking: bool,
    ) -> str:
        view = memoryview(buffer)
        if not view.c_contiguous:
            view.release()
            raise TypeError("output must expose one contiguous Python buffer")
        byte_view = view.cast("B")
        if byte_view is not view:
            view.release()
        size = byte_view.nbytes
        format_id = self._resolve_format_id(format_value)
        if (
            size > int(self.configuration["maxFrameBytes"])
            or size > int(self.configuration["egressCreditBytes"])
            or flags < 0
            or flags > 0xFFFF
            or flags & 0xFF00 & ~DUPLEX_SUPPORTED_REQUIRED_FLAG_MASK
        ):
            byte_view.release()
            raise ValueError("output violates negotiated format, size, or flags")
        processed = (
            None
            if processed_input_through is None
            else int(processed_input_through)
        )
        with self.cv:
            if self.cancellation_reason is not None:
                byte_view.release()
                return f"cancelled:{self.cancellation_reason}"
            if self.terminal is not None:
                byte_view.release()
                raise RuntimeError("duplex session is closed")
            if self.output_finished:
                byte_view.release()
                raise RuntimeError("duplex output is already finished")
            if processed is not None and (
                processed > self.input_accepted_through
                or processed < self.input_processed_through
            ):
                byte_view.release()
                raise ValueError(
                    "processed input watermark regressed or is ahead of "
                    "accepted input"
                )
            while (
                self.available_egress_frames == 0
                or self.available_egress_bytes < size
            ):
                if not blocking:
                    byte_view.release()
                    return "would_block"
                self.cv.wait()
                if self.cancellation_reason is not None:
                    byte_view.release()
                    return f"cancelled:{self.cancellation_reason}"
                if self.terminal is not None or self.output_finished:
                    byte_view.release()
                    raise RuntimeError("duplex session is closed")
            if self.output_produced_through >= (1 << 64) - 1:
                byte_view.release()
                raise ValueError("duplex output sequence exhausted")
            if self.output_produced_bytes > (1 << 64) - 1 - size:
                byte_view.release()
                raise ValueError("duplex output byte counter exhausted")
            sequence = self.output_produced_through + 1
            self.output_produced_through = sequence
            self.output_produced_bytes += size
            self.available_egress_frames -= 1
            self.available_egress_bytes -= size

        try:
            self._enqueue(
                kind=DUPLEX_MEDIA_ENVELOPE_KINDS["data"],
                direction=DUPLEX_MEDIA_DIRECTIONS["egress"],
                flags=flags,
                sequence=sequence,
                timestamp_ns=timestamp_ns,
                format_id=format_id,
                processed_through=processed,
                payload=byte_view,
            )
        except BaseException:
            byte_view.release()
            raise

        if processed is not None:
            with self.cv:
                if self.terminal is not None:
                    raise _GuestDuplexError("duplex session closed during output send")
                if processed > self.input_processed_through:
                    previous_processed = self.input_processed_through
                    self.processed_ingress_completion.declare_completed(
                        processed
                    )
                    if self.input_processed_through > previous_processed:
                        self._send_event(
                            0,
                            {"inputProcessed": {"through": self.input_processed_through}},
                        )
        self._send_event(
            0,
            {"outputProduced": {"through": sequence}},
        )
        return "sent"

    def finish_output_from_python(self):
        with self.cv:
            if self.output_finished:
                return
            if self.cancellation_reason is not None:
                raise RuntimeError(
                    f"duplex session cancelled: {self.cancellation_reason}"
                )
            if self.terminal is not None:
                raise RuntimeError("duplex session is closed")
            self.output_finished = True
            final_sequence = self.output_produced_through
            self.cv.notify_all()
        self._enqueue_structural(
            kind=DUPLEX_MEDIA_ENVELOPE_KINDS["directionEnd"],
            direction=DUPLEX_MEDIA_DIRECTIONS["egress"],
            sequence=final_sequence,
        )

    def record_output_discontinuity(
        self,
        frames: int,
        duration_ns: int,
        reason,
    ):
        if frames <= 0:
            raise ValueError("discontinuity frames must be positive")
        reason_code = self._resolve_reason_code(reason)
        with self.cv:
            if self.cancellation_reason is not None:
                raise RuntimeError(
                    f"duplex session cancelled: {self.cancellation_reason}"
                )
            if self.terminal is not None or self.output_finished:
                raise RuntimeError("duplex output is already finished")
            while self.available_egress_frames == 0:
                self.cv.wait()
                if self.cancellation_reason is not None:
                    raise RuntimeError(
                        f"duplex session cancelled: {self.cancellation_reason}"
                    )
                if self.terminal is not None or self.output_finished:
                    raise RuntimeError("duplex session is closed")
            first = self.output_produced_through + 1
            last = first + frames - 1
            if last >= (1 << 64):
                raise ValueError("duplex output sequence exhausted")
            self.available_egress_frames -= 1
            self.output_produced_through = last
        self._enqueue_structural(
            kind=DUPLEX_MEDIA_ENVELOPE_KINDS["discontinuity"],
            direction=DUPLEX_MEDIA_DIRECTIONS["egress"],
            sequence=first,
            last_sequence=last,
            duration_ns=duration_ns,
            reason_code=reason_code,
        )
        self._send_event(0, {"outputProduced": {"through": last}})

    def _resolve_reason_code(self, reason) -> int:
        if isinstance(reason, int):
            if 0 <= reason <= 0xFFFFFFFF:
                return reason
            raise ValueError("discontinuity reason must fit in UInt32")
        name = str(reason)
        mapping = {
            "unknown": 0,
            "dropped": 1,
            "overrun": 2,
            "routeChange": 3,
        }
        if name not in mapping:
            raise ValueError(f"unknown discontinuity reason {name}")
        return mapping[name]

    def ready_from_python(self, metadata):
        with self.cv:
            if (
                self.terminal is not None
                or self.cancellation_reason is not None
                or self.ready_sent
            ):
                raise ValueError("ready may be sent exactly once before terminal")
            if (
                self.configuration.get(
                    "accelerator",
                    {"kind": "none"},
                ).get("kind") == "mlx"
                and not self.manager.accelerator_lane.is_warmed(self.token)
            ):
                raise ValueError(
                    "MLX ready requires representative lane warm-up "
                    "and synchronization"
                )
            self.ready_sent = True
        if metadata is not None:
            self._send_application_event(
                "readyMetadata",
                payload=metadata,
            )
        self._send_event(0, {"ready": {}})

    def send_event_from_python(
        self,
        kind: str,
        payload,
        produced_through,
        processed_input_through,
    ):
        if (
            not kind
            or len(kind.encode("utf-8")) > 256
            or kind.startswith("swiftpython.")
            or kind.startswith("controlApplied.")
            or kind.startswith("controlRejected.")
            or kind.startswith("interruptionCompleted.")
        ):
            raise ValueError(
                "application event kind is empty, reserved, or too large"
            )
        with self.cv:
            if self.terminal is not None or self.cancellation_reason is not None:
                raise RuntimeError("duplex session is closed")
            actual_produced = self.output_produced_through
            actual_processed = self.input_processed_through
            produced = (
                actual_produced
                if produced_through is None
                else int(produced_through)
            )
            processed = (
                actual_processed
                if processed_input_through is None
                else int(processed_input_through)
            )
            if (
                produced > actual_produced
                or processed > self.input_accepted_through
            ):
                raise ValueError(
                    "application event watermark is ahead of session progress"
                )
        self._send_application_event(
            kind,
            payload=payload,
            input_processed_through=processed,
            output_produced_through=produced,
        )

    def interruption_completed_from_python(
        self,
        interruption_id: str,
        disposition: str,
    ):
        allowed = {
            "truncated",
            "generation_stopped_but_state_not_truncated",
            "already_finished",
            "unsupported",
        }
        if disposition not in allowed:
            raise ValueError(
                "interruption disposition does not describe actual state handling"
            )
        normalized_id = str(uuid.UUID(interruption_id)).upper()
        with self.cv:
            if normalized_id not in self.pending_interruption_ids:
                raise ValueError(
                    "interruption completion is unknown or was already reported"
                )
            self.pending_interruption_ids.remove(normalized_id)
        self._send_application_event(
            f"interruptionCompleted.{normalized_id}.{disposition}"
        )

    def application_control(
        self,
        sequence: int,
        kind: str,
        payload_descriptor,
        acknowledged_output_through,
    ):
        self._validate_control_sequence(sequence)
        if (
            not kind
            or len(kind.encode("utf-8")) > 256
            or kind.startswith((
                "swiftpython.",
                "controlApplied.",
                "controlRejected.",
                "interruptionCompleted.",
            ))
        ):
            raise _GuestDuplexError(
                "application control kind is empty, reserved, or too large"
            )
        with self.cv:
            if self.terminal is not None or self.cancellation_reason is not None:
                raise _GuestDuplexError("duplex session is closed")
            at_capacity = (
                len(self.application_controls)
                >= DUPLEX_MAXIMUM_PYTHON_CONTROL_EVENTS
            )
        if at_capacity:
            self._send_application_event(
                f"controlRejected.{sequence}.resourceLimit",
                control_sequence=sequence,
            )
            return
        payload = (
            None
            if payload_descriptor is None
            else self.worker._resolve_value_descriptor(payload_descriptor)
        )
        rejected_for_capacity = False
        with self.cv:
            if self.terminal is not None or self.cancellation_reason is not None:
                raise _GuestDuplexError("duplex session is closed")
            if (
                len(self.application_controls)
                >= DUPLEX_MAXIMUM_PYTHON_CONTROL_EVENTS
            ):
                rejected_for_capacity = True
            else:
                self.application_controls.append({
                    "kind": "application",
                    "sequence": sequence,
                    "control_kind": kind,
                    "payload": payload,
                    "acknowledged_output_through": acknowledged_output_through,
                })
                self.cv.notify_all()
        if rejected_for_capacity:
            self._send_application_event(
                f"controlRejected.{sequence}.resourceLimit",
                control_sequence=sequence,
            )
            return
        self._send_application_event(
            f"controlApplied.{sequence}.{kind}",
            control_sequence=sequence,
        )

    def interrupt(
        self,
        sequence: int,
        interruption_id: str,
        reason: str,
        consumed_output_through,
    ):
        self._validate_control_sequence(sequence)
        normalized_id = str(uuid.UUID(interruption_id)).upper()
        rejected_for_capacity = False
        with self.cv:
            if self.terminal is not None or self.cancellation_reason is not None:
                raise _GuestDuplexError("duplex session is closed")
            if self.interruption_count >= (1 << 64) - 1:
                raise _GuestDuplexResourceError(
                    "duplex interruption generation exhausted"
                )
            if (
                len(self.pending_interruption_ids)
                >= DUPLEX_MAXIMUM_PYTHON_INTERRUPTION_EVENTS
            ):
                rejected_for_capacity = True
            else:
                self.interruption_count += 1
                event = {
                    "kind": "interrupted",
                    "sequence": sequence,
                    "id": normalized_id,
                    "reason": reason,
                    "consumed_output_through": consumed_output_through,
                    "generation": self.interruption_count,
                }
                self.pending_interruption_ids.add(normalized_id)
                self.latest_interruption_value = dict(event)
                self.interruptions.append(event)
                self.cv.notify_all()
        if rejected_for_capacity:
            self._send_application_event(
                f"interruptionCompleted.{normalized_id}.unsupported"
            )

    def output_acknowledged(self, sequence: int, consumed_through: dict):
        self._validate_control_sequence(sequence)
        consumed_sequence = int(consumed_through["sequence"])
        consumed_offset = int(consumed_through["byteOffset"])
        with self.cv:
            current = self.output_acknowledged_through
            regressed = current is not None and (
                consumed_sequence < int(current["sequence"])
                or (
                    consumed_sequence == int(current["sequence"])
                    and consumed_offset < int(current["byteOffset"])
                )
            )
            if (
                consumed_sequence <= 0
                or consumed_sequence > self.output_produced_through
                or consumed_offset < 0
                or consumed_offset > int(self.configuration["maxFrameBytes"])
                or regressed
            ):
                raise _GuestDuplexError(
                    "output acknowledgement regressed or exceeds production"
                )
            self.output_acknowledged_through = {
                "sequence": consumed_sequence,
                "byteOffset": consumed_offset,
                "sampleOffset": consumed_through.get("sampleOffset"),
            }

    def cancel(self, sequence: int, reason: str):
        with self.cv:
            if sequence != (1 << 64) - 1 and sequence <= self.last_control_sequence:
                return
            self.last_control_sequence = sequence
            if self.cancellation_reason is None:
                self.cancellation_reason = reason
            self.cv.notify_all()
        self.manager.accelerator_lane.cancel(self.token)
        self.manager.cancel_callback_waiters(
            self.control_channel_id,
            reason,
        )

    def close(self, sequence: int):
        with self.cv:
            self.last_control_sequence = max(
                self.last_control_sequence,
                sequence,
            )
        self.finish(
            {"cancelled": {"reason": "user"}},
            drain_writer=False,
            emit_terminal=False,
        )

    def cancel_reason(self):
        with self.cv:
            return self.cancellation_reason

    def interruption_generation(self) -> int:
        with self.cv:
            return self.interruption_count

    def latest_interruption(self):
        with self.cv:
            return (
                None
                if self.latest_interruption_value is None
                else dict(self.latest_interruption_value)
            )

    def handler_thread_id(self) -> int:
        with self.cv:
            return self.handler_ident

    def python_configuration(self) -> dict:
        accelerator = self.configuration.get(
            "accelerator",
            {"kind": "none"},
        )
        return {
            "media_protocol_version":
                int(self.configuration["mediaProtocolVersion"]),
            "ingress_credit_frames":
                int(self.configuration["ingressCreditFrames"]),
            "ingress_credit_bytes":
                int(self.configuration["ingressCreditBytes"]),
            "egress_credit_frames":
                int(self.configuration["egressCreditFrames"]),
            "egress_credit_bytes":
                int(self.configuration["egressCreditBytes"]),
            "max_frame_bytes":
                int(self.configuration["maxFrameBytes"]),
            "input_buffer_route": "leased_owned_copy",
            "output_buffer_route": (
                "leased_exporter_then_uds_copy"
                if self.manager.transport_mode == "uds"
                else "leased_exporter_then_vsock_copy"
            ),
            "bytes_conversion": "explicit_copy",
            "accelerator": {
                "kind": accelerator.get("kind", "none"),
                "lane_name": accelerator.get("laneName", ""),
                "maximum_queued_steps":
                    int(accelerator.get("maximumQueuedSteps", 0)),
                "maximum_active_sessions":
                    int(accelerator.get("maximumActiveSessions", 0)),
                "maximum_resident_models":
                    int(accelerator.get("maximumResidentModels", 0)),
                "maximum_resident_bytes":
                    int(accelerator.get("maximumResidentBytes", 0)),
                "maximum_simultaneous_leases":
                    int(accelerator.get("maximumSimultaneousLeases", 0)),
                "default_model_ttl_ms":
                    int(
                        accelerator.get(
                            "defaultModelTTLMilliseconds",
                            0,
                        )
                    ),
                "cache_clear_minimum_interval_ms":
                    int(
                        accelerator.get(
                            "cacheClearMinimumIntervalMilliseconds",
                            0,
                        )
                    ),
                "warmup_timeout_ms":
                    int(accelerator.get("warmupTimeoutMilliseconds", 0)),
                "maximum_process_lanes":
                    int(accelerator.get("maximumProcessLanes", 0)),
                "startup_stress_probe":
                    accelerator.get("startupStressProbe"),
                "scheduling_weight":
                    int(accelerator.get("schedulingWeight", 0)),
                "maximum_state_items":
                    int(accelerator.get("maximumStateItems", 0)),
                "maximum_state_bytes":
                    int(accelerator.get("maximumStateBytes", 0)),
                "soft_pressure_permille":
                    int(accelerator.get("softPressureRatioPermille", 0)),
                "throttle_pressure_permille":
                    int(
                        accelerator.get(
                            "throttlePressureRatioPermille",
                            0,
                        )
                    ),
                "shed_pressure_permille":
                    int(accelerator.get("shedPressureRatioPermille", 0)),
            },
            "formats": [
                {
                    "id": int(item["id"]),
                    "kind": item["kind"],
                    "metadata": dict(item.get("metadata", {})),
                }
                for item in self.configuration["formats"]
            ],
        }

    def _validate_control_sequence(self, sequence: int):
        with self.cv:
            if self.terminal is not None or sequence <= self.last_control_sequence:
                raise _GuestDuplexError("duplex control sequence regressed")
            self.last_control_sequence = sequence

    def _pickle_descriptor(self, value):
        encoded = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        if len(encoded) > DUPLEX_MAXIMUM_CONTROL_PAYLOAD_BYTES:
            raise ValueError("duplex application payload exceeds control bound")
        return {
            "pickle": {
                "_0": base64.b64encode(encoded).decode("ascii"),
            },
        }

    def _send_application_event(
        self,
        kind: str,
        *,
        payload=None,
        input_processed_through=None,
        output_produced_through=None,
        control_sequence: int = 0,
    ):
        with self.cv:
            processed = (
                self.input_processed_through
                if input_processed_through is None
                else input_processed_through
            )
            produced = (
                self.output_produced_through
                if output_produced_through is None
                else output_produced_through
            )
        descriptor = None if payload is None else self._pickle_descriptor(payload)
        self._send_event(
            control_sequence,
            {
                "application": {
                    "kind": kind,
                    "payload": descriptor,
                    "inputProcessedThrough": processed,
                    "outputProducedThrough": produced,
                },
            },
        )

    def _send_event(self, control_sequence: int, event: dict):
        self.worker._send_response(
            "duplexEvent",
            {
                "sessionID": self.session_id,
                "controlSequence": int(control_sequence),
                "event": event,
            },
            b"",
            channel_id=self.control_channel_id,
        )

    def _enqueue_structural(self, **values):
        self._enqueue(**values)

    def _enqueue(self, **values):
        header, payload_view = _duplex_encode_envelope(**values)
        try:
            self.writer_queue.put((header, payload_view), timeout=2)
        except queue.Full as error:
            if payload_view is not None:
                payload_view.release()
            raise _GuestDuplexError(
                "guest duplex writer queue exhausted"
            ) from error

    def fail(self, code: str, message: str):
        self.finish(
            {"failed": {"code": code, "message": str(message)}},
            drain_writer=False,
        )

    def finish(
        self,
        terminal: dict,
        *,
        drain_writer: bool,
        emit_terminal: bool = True,
    ):
        with self.cv:
            if self.terminal is not None:
                return
            self.terminal = terminal
            self.pending_interruption_ids.clear()
            watermarks = (
                self.input_accepted_through,
                self.input_processed_through,
                self.output_produced_through,
                self.output_acknowledged_through,
                self.last_control_sequence,
            )
            self.cv.notify_all()
        self.manager.cancel_callback_waiters(
            self.control_channel_id,
            "terminal",
        )
        if emit_terminal:
            self.worker._send_response(
                "duplexTerminal",
                {
                    "sessionID": self.session_id,
                    "controlSequence": watermarks[4],
                    "terminal": terminal,
                    "inputAcceptedThrough": watermarks[0],
                    "inputProcessedThrough": watermarks[1],
                    "outputProducedThrough": watermarks[2],
                    "outputAcknowledgedThrough": watermarks[3],
                },
                b"",
                channel_id=self.control_channel_id,
            )
        self._begin_cleanup(drain_writer)

    def _begin_cleanup(self, drain_writer: bool):
        with self.cv:
            if self.cleanup_started:
                return
            self.cleanup_started = True

        def cleanup():
            try:
                if drain_writer:
                    try:
                        self.writer_queue.put(None, timeout=2)
                    except queue.Full:
                        pass
                    if self.writer_thread is not None:
                        self.writer_thread.join(timeout=2)
                else:
                    while True:
                        try:
                            item = self.writer_queue.get_nowait()
                        except queue.Empty:
                            break
                        if item is not None and item[1] is not None:
                            item[1].release()
                    try:
                        self.sock.shutdown(socket.SHUT_RDWR)
                    except Exception:
                        pass
                    try:
                        self.writer_queue.put_nowait(None)
                    except queue.Full:
                        pass
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self.sock.close()
                except Exception:
                    pass
                for thread in (self.reader_thread, self.handler_thread):
                    if (
                        thread is not None
                        and thread is not threading.current_thread()
                    ):
                        thread.join(timeout=2)
            finally:
                with self.cv:
                    self.application_controls.clear()
                    self.interruptions.clear()
                    self.pending_interruption_ids.clear()
                    self.ingress.clear()
                    self.ingress_bytes = 0
                    self.cv.notify_all()
                self.manager.remove(self)
                self.cleanup_done.set()

        threading.Thread(
            target=cleanup,
            name=f"swiftpython-vm-duplex-cleanup-{self.token}",
            daemon=True,
        ).start()

    def wait_for_cleanup(self, timeout: float) -> bool:
        return self.cleanup_done.wait(timeout)

    def _is_terminal(self) -> bool:
        with self.cv:
            return self.terminal is not None
