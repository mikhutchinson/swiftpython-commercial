#!/usr/bin/env python3
"""SwiftPython VM Worker — Pure Python implementation of the MessageFrame IPC protocol.

Speaks the same wire protocol as the compiled SwiftPythonWorker binary, but runs
natively inside a macOS guest VM. Communicates with the host via AF_VSOCK.

Wire format (v5 — binary sidecar + channel IDs on every command/response):
    ┌──────────────┬──────────────┬──────────────┬─────────────────┬──────────────────┐
    │ JSONLen (4B) │ BinLen (4B)  │ Type (1B)    │ JSON Payload    │ Binary Payload   │
    │ UInt32 LE    │ UInt32 LE    │ 0=Cmd 1=Resp │ Variable length │ Variable length  │
    └──────────────┴──────────────┴──────────────┴─────────────────┴──────────────────┘

Binary sidecar for RemoteValueDescriptors:
    [UInt32 entryCount][UInt32 len0][UInt32 len1]...[bytes0][bytes1]...
"""

import ast
import json
import os
import pickle
import queue
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADER_SIZE = 9  # 4 (json_len) + 4 (bin_len) + 1 (type)
MSG_TYPE_COMMAND = 0
MSG_TYPE_RESPONSE = 1
MSG_TYPE_SIDE = 2

HOST_CID = 2  # vsock host CID

MAX_PAYLOAD_BYTES = 16 * 1024 * 1024  # 16 MB default
CURRENT_PROTOCOL_VERSION = 5

# ---------------------------------------------------------------------------
# MessageFrame encoding / decoding
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Binary sidecar helpers (RemoteValueDescriptor pickle extraction/injection)
# ---------------------------------------------------------------------------


def extract_binary_from_command(cmd_name: str, cmd_data: dict) -> bytes:
    """Extract binary sidecar bytes from a decoded command."""
    if cmd_name == "store":
        return b""  # placeholder in JSON, real data in binary sidecar
    if cmd_name in ("callbackResult", "callbackStreamChunk"):
        return b""
    if cmd_name in ("invoke", "invokeResult", "method", "methodResult",
                     "methodStream", "invokeStream"):
        return _extract_from_remote_value_descriptors(
            cmd_data.get("args", []),
            cmd_data.get("kwargs", {}),
        )
    return b""


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
    if cmd_name in ("invoke", "invokeResult", "method", "methodResult",
                     "methodStream", "invokeStream"):
        args = cmd_data.get("args", [])
        kwargs = cmd_data.get("kwargs", {})
        new_args, new_kwargs = _inject_into_remote_value_descriptors(args, kwargs, binary)
        cmd_data["args"] = new_args
        cmd_data["kwargs"] = new_kwargs
        return cmd_data
    return cmd_data


def extract_binary_from_response(resp_name: str, resp_data: dict) -> bytes:
    """Extract binary sidecar from a response before JSON encoding."""
    if resp_name == "result":
        data = resp_data.get("_0", b"")
        resp_data["_0"] = ""  # base64 placeholder
        return data if isinstance(data, bytes) else b""
    if resp_name == "streamChunk":
        data = resp_data.get("_0", b"")
        resp_data["_0"] = ""
        return data if isinstance(data, bytes) else b""
    if resp_name in ("callbackInvocation", "callbackAsyncInvocation"):
        data = resp_data.get("argsPickle", b"")
        resp_data["argsPickle"] = ""
        return data if isinstance(data, bytes) else b""
    return b""


def _extract_from_remote_value_descriptors(args: list, kwargs: dict) -> bytes:
    pickle_entries = []
    for arg in args:
        if "pickle" in arg:
            pickle_entries.append(arg["pickle"].get("_0", b""))
    for key in sorted(kwargs.keys()):
        val = kwargs[key]
        if "pickle" in val:
            pickle_entries.append(val["pickle"].get("_0", b""))
    if not pickle_entries:
        return b""
    header = struct.pack("<I", len(pickle_entries))
    for entry in pickle_entries:
        header += struct.pack("<I", len(entry) if isinstance(entry, bytes) else 0)
    for entry in pickle_entries:
        if isinstance(entry, bytes):
            header += entry
    return header


def _inject_into_remote_value_descriptors(
    args: list, kwargs: dict, binary: bytes
) -> tuple:
    if len(binary) < 4:
        return args, kwargs
    entry_count = struct.unpack_from("<I", binary, 0)[0]
    header_bytes = 4 + entry_count * 4
    if len(binary) < header_bytes:
        return args, kwargs

    lengths = []
    for i in range(entry_count):
        length = struct.unpack_from("<I", binary, 4 + i * 4)[0]
        lengths.append(length)

    entries = []
    offset = header_bytes
    for length in lengths:
        entries.append(binary[offset:offset + length])
        offset += length

    entry_idx = 0
    new_args = []
    for arg in args:
        if "pickle" in arg and entry_idx < len(entries):
            arg["pickle"]["_0"] = entries[entry_idx]
            entry_idx += 1
        new_args.append(arg)

    new_kwargs = {}
    for key in sorted(kwargs.keys()):
        val = kwargs[key]
        if "pickle" in val and entry_idx < len(entries):
            val["pickle"]["_0"] = entries[entry_idx]
            entry_idx += 1
        new_kwargs[key] = val

    return new_args, new_kwargs


# ---------------------------------------------------------------------------
# Socket I/O helpers
# ---------------------------------------------------------------------------


def recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    """Read exactly nbytes from sock, raising on EOF."""
    buf = bytearray()
    while len(buf) < nbytes:
        chunk = sock.recv(nbytes - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by peer")
        buf.extend(chunk)
    return bytes(buf)


def send_all(sock: socket.socket, data: bytes):
    """Send all bytes, handling partial sends."""
    sock.sendall(data)


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


class Worker:
    def __init__(self, sock: socket.socket, worker_id: int, ipc_config: dict):
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

        # Abort flag for cooperative stream cancellation
        self.abort_requested = False
        self.streaming_active = False
        self.cancel_flags: dict[int, threading.Event] = {}
        self.cancel_flags_lock = threading.Lock()
        self.active_command_channel = threading.local()
        self.active_stream_channel = threading.local()

        # Side channel
        self.side_sock: socket.socket | None = None
        self.side_thread: threading.Thread | None = None
        self.side_stopping = False

        # Callbacks (bidirectional IPC with Swift host)
        self._registered_callbacks: set[str] = set()
        self._swift_bridge_installed = False
        self._next_call_id: int = 1
        self._next_call_id_lock = threading.Lock()
        self._callback_waiters: dict[int, "queue.Queue[tuple[str, dict, bytes]]"] = {}
        self._callback_waiters_lock = threading.Lock()
        self._async_callback_waiters: dict[int, tuple["queue.Queue[tuple[str, dict, bytes]]", int]] = {}
        self._async_callback_waiters_lock = threading.Lock()
        self._active_stream_iterators: dict[int, object] = {}
        self._active_stream_iterators_lock = threading.Lock()
        self._send_lock = threading.Lock()

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
        self.side_thread = threading.Thread(
            target=self._side_channel_loop, daemon=True
        )
        self.side_thread.start()

    def _side_channel_loop(self):
        """Read side channel commands until the socket closes."""
        sock = self.side_sock
        try:
            while not self.side_stopping:
                try:
                    header_bytes = recv_exact(sock, HEADER_SIZE)
                except ConnectionError:
                    break
                json_len, bin_len, msg_type = struct.unpack_from("<IIB", header_bytes)
                if msg_type != MSG_TYPE_SIDE and msg_type != MSG_TYPE_COMMAND:
                    # Skip unknown message types
                    if json_len + bin_len > 0:
                        recv_exact(sock, json_len + bin_len)
                    continue
                payload = recv_exact(sock, json_len + bin_len)
                json_payload = payload[:json_len]
                cmd = json.loads(json_payload)
                cmd_name = next(iter(cmd))
                cmd_data = cmd[cmd_name]
                self._dispatch_side_command(cmd_name, cmd_data)
        except Exception as e:
            if not self.side_stopping:
                print(f"[worker {self.worker_id}] side channel error: {e}", file=sys.stderr, flush=True)
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _dispatch_side_command(self, cmd_name: str, cmd_data: dict):
        """Execute a side channel command. Fire-and-forget — no response sent."""
        if cmd_name == "eval":
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
                    oob_sock.sendall(struct.pack("<I", len(data)) + data)
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
        stream_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="swiftpython-vm-stream")
        command_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="swiftpython-vm-command")
        child_command_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="swiftpython-vm-child")
        while self.running:
            try:
                cmd_name, cmd_data, binary = self._receive_command()
                cmd_data = inject_binary_into_command(cmd_name, cmd_data, binary)

                if self._route_callback_reply(cmd_name, cmd_data, binary):
                    continue

                if cmd_name in ("methodStream", "invokeStream", "evalStream"):
                    stream_pool.submit(self._execute_stream, cmd_name, cmd_data)
                elif cmd_name == "shutdown":
                    self._handle_and_send(cmd_name, cmd_data)
                    break
                else:
                    target_pool = child_command_pool if self._has_active_callback_waiters() else command_pool
                    target_pool.submit(self._handle_and_send, cmd_name, cmd_data)
            except ConnectionError:
                break
            except Exception as e:
                if self.sock.fileno() < 0:
                    break
                try:
                    self._send_response(
                        "error",
                        {"code": "unknown", "message": f"Worker error: {e}"},
                        b"",
                    )
                except Exception:
                    break
        stream_pool.shutdown(wait=False, cancel_futures=True)
        command_pool.shutdown(wait=False, cancel_futures=True)
        child_command_pool.shutdown(wait=False, cancel_futures=True)
        self._fail_all_callback_waiters(ConnectionError("worker shutdown"))
        self._fail_all_async_callback_waiters(ConnectionError("worker shutdown"))

    def _handle_and_send(self, cmd_name: str, cmd_data: dict):
        channel_id = self._command_channel_id(cmd_name, cmd_data)
        self.active_command_channel.channel_id = channel_id
        try:
            resp_name, resp_data, resp_binary = self._handle_command(cmd_name, cmd_data)
            self._send_response(resp_name, resp_data, resp_binary, channel_id=channel_id)
        except Exception as e:
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
        return int(cmd_data.get("channelID", 0) or 0)

    def _current_callback_channel_id(self) -> int:
        stream_channel = int(getattr(self.active_stream_channel, "channel_id", 0) or 0)
        if stream_channel:
            return stream_channel
        return int(getattr(self.active_command_channel, "channel_id", 0) or 0)

    def _next_callback_call_id(self) -> int:
        with self._next_call_id_lock:
            call_id = self._next_call_id
            self._next_call_id += 1
            return call_id

    def _register_callback_waiter(self, call_id: int) -> "queue.Queue[tuple[str, dict, bytes]]":
        waiter: "queue.Queue[tuple[str, dict, bytes]]" = queue.Queue()
        with self._callback_waiters_lock:
            self._callback_waiters[call_id] = waiter
        return waiter

    def _unregister_callback_waiter(self, call_id: int):
        with self._callback_waiters_lock:
            self._callback_waiters.pop(call_id, None)

    def _register_async_callback_waiter(self, call_id: int, channel_id: int) -> "queue.Queue[tuple[str, dict, bytes]]":
        waiter: "queue.Queue[tuple[str, dict, bytes]]" = queue.Queue()
        with self._async_callback_waiters_lock:
            self._async_callback_waiters[call_id] = (waiter, channel_id)
        return waiter

    def _async_callback_waiter(self, call_id: int) -> tuple["queue.Queue[tuple[str, dict, bytes]]", int] | None:
        with self._async_callback_waiters_lock:
            return self._async_callback_waiters.get(call_id)

    def _unregister_async_callback_waiter(self, call_id: int):
        with self._async_callback_waiters_lock:
            self._async_callback_waiters.pop(call_id, None)

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
            waiter = self._callback_waiters.get(call_id)
        if waiter is not None:
            waiter.put((cmd_name, cmd_data, binary))
        return True

    def _fail_all_callback_waiters(self, error: Exception):
        with self._callback_waiters_lock:
            waiters = list(self._callback_waiters.values())
            self._callback_waiters.clear()
        for waiter in waiters:
            waiter.put(("__error__", {"message": str(error)}, b""))

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
        header_bytes = recv_exact(self.sock, HEADER_SIZE)
        json_len, bin_len, msg_type = struct.unpack_from("<IIB", header_bytes)

        total = json_len + bin_len
        if total > self.max_payload:
            raise ValueError(f"Payload too large: {total} > {self.max_payload}")

        payload = recv_exact(self.sock, total)
        json_payload = payload[:json_len]
        binary_payload = payload[json_len:]

        if msg_type != MSG_TYPE_COMMAND:
            raise ValueError(f"Expected command (type 0), got type {msg_type}")

        cmd = json.loads(json_payload)
        # Swift Codable enum: {"caseName": {associated_values}}
        cmd_name = next(iter(cmd))
        cmd_data = cmd[cmd_name]

        return cmd_name, cmd_data, binary_payload

    def _send_response(self, resp_name: str, resp_data: dict, binary: bytes, channel_id: int | None = None):
        """Encode and send a framed response."""
        data = dict(resp_data)
        if resp_name == "healthy":
            data.setdefault("protocolVersion", CURRENT_PROTOCOL_VERSION)
        if channel_id is not None:
            if resp_name in ("streamChunk", "streamEnd", "streamKeepalive", "streamProgress"):
                data.setdefault("streamChannelID", channel_id)
            else:
                data.setdefault("channelID", channel_id)
        resp = {resp_name: data}
        json_bytes = json.dumps(resp, separators=(",", ":"), default=_json_default).encode("utf-8")
        frame = encode_frame(MSG_TYPE_RESPONSE, json_bytes, binary)
        with self._send_lock:
            send_all(self.sock, frame)

    def _handle_command(self, cmd_name: str, cmd_data: dict) -> tuple:
        """Dispatch a command, returning (resp_name, resp_data, binary)."""
        if cmd_name == "healthCheck":
            return "healthy", {"protocolVersion": CURRENT_PROTOCOL_VERSION}, b""

        if cmd_name == "shutdown":
            self.running = False
            return "success", {}, b""

        if cmd_name == "eval":
            return self._execute_eval(cmd_data)

        if cmd_name in ("invoke", "invokeResult"):
            return self._execute_invoke(cmd_data, pickle_result=(cmd_name == "invokeResult"))

        if cmd_name in ("method", "methodResult"):
            return self._execute_method(cmd_data, pickle_result=(cmd_name == "methodResult"))

        if cmd_name == "streamCancel":
            self._signal_stream_cancel(int(cmd_data.get("streamChannelID", 0) or 0))
            return "success", {}, b""

        if cmd_name == "store":
            return self._store_object(cmd_data)

        if cmd_name == "release":
            return self._release_object(cmd_data)

        if cmd_name == "setResourceLimits":
            return self._set_resource_limits(cmd_data)

        if cmd_name == "getArrayInfo":
            return self._get_array_info(cmd_data)

        if cmd_name == "copyToShared":
            return self._copy_to_shared(cmd_data)

        if cmd_name == "attachSharedMemory":
            return self._attach_shared_memory(cmd_data)

        if cmd_name == "registerCallback":
            return self._register_callback(cmd_data)

        if cmd_name == "unregisterCallback":
            return self._unregister_callback(cmd_data)

        return "error", {"code": "internalError", "message": f"Unknown command: {cmd_name}"}, b""

    # -----------------------------------------------------------------------
    # eval
    # -----------------------------------------------------------------------

    def _execute_eval(self, cmd_data: dict) -> tuple:
        code = textwrap.dedent(cmd_data.get("code", ""))
        bindings = cmd_data.get("bindings", {})

        try:
            # Scrub evalResult sentinel keys
            for key in ("__result__", "__swiftpython_return_pickled_result__",
                        "__swiftpython_result_object__", "__swiftpython_pickled_result__"):
                self.namespace.pop(key, None)

            # Add bindings
            for name, descriptor in bindings.items():
                obj = self._resolve_handle(descriptor)
                self.namespace[name] = obj

            # Parse AST to handle trailing expressions like a REPL
            tree = ast.parse(code)
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

            # Check for evalResult flags
            if self.namespace.get("__swiftpython_return_pickled_result__"):
                result_obj = self.namespace.get("__swiftpython_result_object__", result)
                pickled = self.namespace.get("__swiftpython_pickled_result__")
                if pickled is None and result_obj is not None:
                    pickled = pickle.dumps(result_obj, protocol=pickle.HIGHEST_PROTOCOL)
                if pickled is not None:
                    return "result", {"_0": ""}, pickled

            if result is None:
                # Check __result__ fallback
                result = self.namespace.get("__result__")

            if result is not None:
                handle_id = str(uuid.uuid4()).upper()
                with self.object_store_lock:
                    self.object_store[handle_id] = result
                descriptor = {
                    "id": handle_id,
                    "processID": {"worker": {"index": self.worker_id, "generation": 1}},
                    "isShared": False,
                }
                return "handle", {"_0": descriptor}, b""

            return "success", {}, b""

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
        channel_id = self._command_channel_id(cmd_name, cmd_data)
        self.streaming_active = True
        self.abort_requested = False
        self.active_stream_channel.channel_id = channel_id
        self.active_stream_channel.started_ns = time.monotonic_ns()
        self._clear_stream_cancel(channel_id)

        try:
            iterator = self._get_stream_iterator(cmd_name, cmd_data)

            for item in iterator:
                if self.abort_requested or self._is_stream_cancelled(channel_id):
                    break
                pickled = pickle.dumps(item, protocol=pickle.HIGHEST_PROTOCOL)
                self._send_response("streamChunk", {"_0": ""}, pickled, channel_id=channel_id)

            self._send_response("streamEnd", {}, b"", channel_id=channel_id)

        except Exception as e:
            resp_name, resp_data, resp_binary = self._make_python_error(e)
            self._send_response(resp_name, resp_data, resp_binary, channel_id=channel_id)
        finally:
            self.streaming_active = False
            self.abort_requested = False
            self._clear_stream_cancel(channel_id)
            self.active_stream_channel.channel_id = 0
            self.active_stream_channel.started_ns = 0

    def _signal_stream_cancel(self, channel_id: int):
        with self.cancel_flags_lock:
            flag = self.cancel_flags.setdefault(channel_id, threading.Event())
            flag.set()

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
            obj = self.object_store.get(handle_id)
        if obj is None:
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
        waiter = self._register_callback_waiter(call_id)

        args_json = json.dumps(args, default=_json_default).encode("utf-8")
        channel_id = self._current_callback_channel_id()
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
        waiter, channel_id = entry

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
        waiter = self._register_callback_waiter(call_id)
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
            obj = self.object_store.get(handle_id)
        if obj is None:
            raise KeyError(f"Handle not found: {handle_id}")
        return obj

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
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        exc_tb = traceback.format_exc()
        return "pythonError", {
            "type": exc_type,
            "message": exc_msg,
            "traceback": exc_tb,
        }, b""


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


def connect_vsock(host_cid: int, port: int) -> socket.socket:
    """Connect to host via AF_VSOCK."""
    AF_VSOCK = 40  # macOS AF_VSOCK
    sock = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    sock.connect((host_cid, port))
    return sock


def listen_vsock(port: int) -> socket.socket:
    """Listen on a vsock port and accept one connection from the host."""
    AF_VSOCK = 40
    VSOCK_CID_ANY = -1
    server = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((VSOCK_CID_ANY, port))
    server.listen(1)
    conn, _ = server.accept()
    server.close()
    return conn


def connect_uds(path: str) -> socket.socket:
    """Connect via Unix domain socket (for testing without a VM)."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    return sock


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
    if socket_arg == "--vsock-listen":
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
        print(f"[worker {worker_id}] listening on vsock port {port}", file=sys.stderr, flush=True)
        sock = listen_vsock(port)
        print(f"[worker {worker_id}] host connected on main port {port}", file=sys.stderr, flush=True)
        if len(sys.argv) >= 6:
            side_port = int(sys.argv[5])
            print(f"[worker {worker_id}] listening on vsock side port {side_port}", file=sys.stderr, flush=True)
            side_sock = listen_vsock(side_port)
            print(f"[worker {worker_id}] host connected on side port {side_port}", file=sys.stderr, flush=True)
    elif socket_arg == "--vsock":
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

    worker = Worker(sock, worker_id, ipc_config)

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
