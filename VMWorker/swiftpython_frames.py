"""MessageFrame encoding, binary-sidecar injection and the socket read/write
helpers for the SwiftPython VM worker. `recv_exact` and `send_all` are the
worker's only socket I/O helpers."""

import json
import socket
import struct
import time
from _swiftpython_duplex import capability_declaration as _duplex_capability_declaration  # noqa: E402
from _swiftpython_wire import SIDECAR_LENGTH_STRUCT  # noqa: E402


STREAM_CREDIT_FEATURE = "stream.credit.v1"


def worker_capability_declaration(protocol_version: int, transport: str) -> dict:
    """The Python worker's capability declaration: the duplex layer's plus the
    features the worker's own run loop implements. The worker answers
    ``describeCapabilities`` with it and the supervisor reports it in
    ``describe``, so the two cannot disagree."""
    declaration = _duplex_capability_declaration(protocol_version, transport)
    # The run loop reads commands while streams run, so credit grants reach a
    # paced producer.
    declaration["features"] = list(declaration.get("features", [])) + [STREAM_CREDIT_FEATURE, "callback.owned-reentry.v1"]
    return declaration


HEADER_SIZE = 9  # 4 (json_len) + 4 (bin_len) + 1 (type)

MSG_TYPE_COMMAND = 0

MSG_TYPE_RESPONSE = 1

MSG_TYPE_SIDE = 2

HOST_CID = 2  # vsock host CID

MAX_PAYLOAD_BYTES = 16 * 1024 * 1024  # 16 MB default

def encode_frame(msg_type: int, json_payload: bytes, binary_payload: bytes = b"") -> bytes:
    header = struct.pack("<IIB", len(json_payload), len(binary_payload), msg_type)
    return header + json_payload + binary_payload

def decode_header(data: bytes):
    if len(data) < HEADER_SIZE:
        return None
    json_len, bin_len, msg_type = struct.unpack_from("<IIB", data)
    return msg_type, json_len, bin_len

def encode_response(response_dict: dict, binary: bytes = b"") -> bytes:
    json_bytes = json.dumps(response_dict, separators=(",", ":")).encode("utf-8")
    return encode_frame(MSG_TYPE_RESPONSE, json_bytes, binary)

def inject_binary_into_command(cmd_name: str, cmd_data: dict, binary: bytes) -> dict:
    """Inject binary sidecar bytes back into a decoded command."""
    if not binary:
        return cmd_data
    if cmd_name == "store":
        cmd_data["pickle"] = binary
        return cmd_data
    if cmd_name == "callbackResult":
        cmd_data["pickle"] = binary
        return cmd_data
    if cmd_name == "callbackStreamChunk":
        cmd_data["pickle"] = binary
        return cmd_data
    if cmd_name in (
        "invoke", "invokeResult", "method", "methodResult",
        "methodStream", "invokeStream", "duplexOpen",
    ):
        argument_key = "arguments" if cmd_name == "duplexOpen" else "args"
        args = cmd_data.get(argument_key, [])
        kwargs = cmd_data.get("kwargs", {})
        new_args, new_kwargs = _inject_into_remote_value_descriptors(args, kwargs, binary)
        cmd_data[argument_key] = new_args
        cmd_data["kwargs"] = new_kwargs
        return cmd_data
    return cmd_data

def _inject_into_remote_value_descriptors(
    args: list, kwargs: dict, binary: bytes
) -> tuple:
    # Validate the entire table and descriptor cardinality before slicing or
    # mutating any caller-owned descriptor. Empty sidecars are handled by the
    # command decoder to preserve legacy JSON-only arguments.
    width = struct.calcsize(SIDECAR_LENGTH_STRUCT)
    if len(binary) < width:
        raise ValueError("Binary sidecar is missing its entry count")
    entry_count = struct.unpack_from(SIDECAR_LENGTH_STRUCT, binary, 0)[0]
    if entry_count > (len(binary) - width) // width:
        raise ValueError("Binary sidecar length table is truncated")
    descriptors = [value for value in args if "pickle" in value]
    descriptors.extend(kwargs[key] for key in sorted(kwargs) if "pickle" in kwargs[key])
    if entry_count != len(descriptors):
        raise ValueError("Binary sidecar entry count does not match pickle descriptors")
    if any(not isinstance(value["pickle"], dict) for value in descriptors):
        raise ValueError("Binary sidecar pickle descriptor is malformed")
    header_bytes = width * (1 + entry_count)
    lengths = [struct.unpack_from(SIDECAR_LENGTH_STRUCT, binary, width * (1 + i))[0]
               for i in range(entry_count)]
    if sum(lengths) != len(binary) - header_bytes:
        raise ValueError("Binary sidecar lengths do not match its data")
    offset = header_bytes
    for descriptor, length in zip(descriptors, lengths):
        descriptor["pickle"]["_0"] = binary[offset:offset + length]
        offset += length
    return args, kwargs


class FrameValidationError(ValueError):
    """An invalid header leaves unread bytes: its connection must be retired."""


def receive_frame(
    sock: socket.socket, max_payload: int, *,
    expected_type: int | None = None, allow_binary: bool = True,
) -> tuple:
    """Validate size and the caller's framing contract before any body read."""
    header = recv_exact(sock, HEADER_SIZE)
    json_len, bin_len, msg_type = struct.unpack_from("<IIB", header)
    total = json_len + bin_len
    if total > max_payload:
        raise FrameValidationError(f"Payload too large: {total} > {max_payload}")
    if expected_type is not None and msg_type != expected_type:
        raise FrameValidationError(f"Unexpected message type: {msg_type}")
    if not allow_binary and bin_len:
        raise FrameValidationError("Binary payload is not supported on this channel")
    payload = recv_exact(sock, total)
    return msg_type, payload[:json_len], payload[json_len:]

def recv_exact(sock: socket.socket, nbytes: int, *, timeout: float | None = None) -> bytes:
    """Read exactly nbytes from sock, raising on EOF.

    This and `send_all` are the worker's only socket read/write helpers.
    `EINTR` is retried by CPython itself (PEP 475), so an interrupted call is
    never a failure here.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    previous_timeout = sock.gettimeout() if deadline is not None else None
    buf = bytearray()
    try:
        while len(buf) < nbytes:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("socket read deadline expired")
                sock.settimeout(remaining)
            chunk = sock.recv(nbytes - len(buf))
            if not chunk:
                raise ConnectionError("Connection closed by peer")
            buf.extend(chunk)
        return bytes(buf)
    finally:
        if deadline is not None:
            sock.settimeout(previous_timeout)


def send_all(sock: socket.socket, *buffers, timeout: float | None = None) -> None:
    """Send all buffers in order. An optional deadline bounds the whole write.

    Callers sharing a socket hold its writer lock; a timed handshake exclusively
    owns its socket and restores the normal session mode before publication.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    previous_timeout = sock.gettimeout() if deadline is not None else None
    try:
        for data in buffers:
            if len(data):
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("socket write deadline expired")
                    sock.settimeout(remaining)
                sock.sendall(data)
    finally:
        if deadline is not None:
            sock.settimeout(previous_timeout)

def connect_vsock(host_cid: int, port: int) -> socket.socket:
    """Connect to host via AF_VSOCK."""
    AF_VSOCK = 40  # macOS AF_VSOCK
    sock = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    sock.connect((host_cid, port))
    return sock

def bind_vsock_listener(
    port: int,
    timeout: float | None = None,
) -> socket.socket:
    """Bind and listen on a vsock port without accepting."""
    AF_VSOCK = 40
    VSOCK_CID_ANY = -1
    server = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if timeout is not None:
            server.settimeout(timeout)
        server.bind((VSOCK_CID_ANY, port))
        server.listen(1)
    except BaseException:
        server.close()
        raise
    return server


def accept_vsock_listener(server: socket.socket) -> socket.socket:
    """Accept one connection from the host and close the listener."""
    try:
        conn, _ = server.accept()
        return conn
    finally:
        server.close()


def listen_vsock(
    port: int,
    timeout: float | None = None,
) -> socket.socket:
    """Listen on a vsock port and accept one connection from the host."""
    return accept_vsock_listener(bind_vsock_listener(port, timeout=timeout))

def connect_uds(path: str) -> socket.socket:
    """Connect via Unix domain socket (for testing without a VM)."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    return sock
