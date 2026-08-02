#!/usr/bin/env python3
"""SwiftPython VM Supervisor — Manages worker processes inside the guest VM.

Runs as a system service inside the VM. Accepts a host connection on AF_VSOCK
and spawns/kills Python worker processes on command. Frames are JSON over the
same 9-byte length-prefixed header as MessageFrame.

The control vocabulary is **not** documented here. Commands are registered with
``@command`` and frames with ``swiftpython_protocol.frame``; the ``describe``
command answers with both registries serialised, and the host derives its
routing from that response at connect time. A prose table in this docstring
would be a third copy of the contract and would rot — the previous one did,
omitting nine of the commands it purported to list.

To see the vocabulary of a running guest, send ``{"describe": {}}``.
"""

import json
import base64
import hashlib
import hmac
import os
import pwd
import resource
import selectors
import shlex
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import pty
import fcntl
import termios

# The supervisor is installed as a bare executable (`/usr/local/bin/
# swiftpython-supervisor`) next to its protocol module. Python already puts the
# script's directory on `sys.path`, but resolve it explicitly so a symlinked or
# renamed install still finds the module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from swiftpython_protocol import (  # noqa: E402
    ABORTED,
    AUTH_CHALLENGE,
    AUTH_FAILED,
    AUTH_OK,
    AUTH_ROTATED,
    CONFIGURED,
    DESCRIBED,
    ERROR,
    EXEC_ERROR,
    EXEC_RESULT,
    EXEC_SIGNAL_ACK,
    EXEC_STARTED,
    EXEC_STDERR,
    EXEC_STDIN_ACK,
    EXEC_STDOUT,
    EXEC_TIMEOUT,
    IDLE_STATUS,
    KILLED,
    SHUTTING_DOWN,
    SNAPSHOT_READY,
    SPAWNED,
    STATUS,
    SUPERVISOR_PROTOCOL_VERSION,
    WORKER_PROTOCOL_VERSION,
    ExecErrorCode,
    QuotaResource,
    command,
    command_spec,
    declaration,
    observed_undeclared_keys,
    registered_commands,
)
from _swiftpython_duplex import capability_declaration as duplex_capability_declaration  # noqa: E402
from _swiftpython_wire import CURRENT_PROTOCOL_VERSION  # noqa: E402


def _guest_artifact_sha256() -> dict[str, str]:
    """Hash the exact five guest files beside this running supervisor."""
    directory = os.path.dirname(os.path.abspath(__file__))
    candidates = {
        "swiftpython_protocol.py": ("swiftpython_protocol.py",),
        "_swiftpython_wire.py": ("_swiftpython_wire.py",),
        "_swiftpython_duplex.py": ("_swiftpython_duplex.py",),
        "swiftpython_supervisor.py": (
            os.path.basename(os.path.abspath(__file__)),
            "swiftpython_supervisor.py",
        ),
        "swiftpython_worker.py": ("swiftpython-worker", "swiftpython_worker.py"),
    }
    hashes: dict[str, str] = {}
    for logical_name, filenames in candidates.items():
        path = next(
            (
                os.path.join(directory, filename)
                for filename in filenames
                if os.path.isfile(os.path.join(directory, filename))
            ),
            None,
        )
        if path is None:
            raise FileNotFoundError(
                f"guest artifact {logical_name} is missing beside supervisor"
            )
        digest = hashlib.sha256()
        with open(path, "rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[logical_name] = digest.hexdigest()
    return hashes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADER_SIZE = 9
MSG_TYPE_COMMAND = 0
MSG_TYPE_RESPONSE = 1
HOST_CID = 2
CONTROL_PORT = 1024
VSOCK_CID_ANY = -1

# Path to the worker script (installed during VM provisioning).
# On macOS guests the scripts are co-located .py files; on Linux (Alpine)
# they're installed as /usr/local/bin/swiftpython-worker (no extension).
def _find_worker_script() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "swiftpython_worker.py"),
        os.path.join(base, "swiftpython-worker"),
        "/usr/local/bin/swiftpython-worker",
        "/usr/local/bin/swiftpython_worker.py",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]  # fallback, will fail with clear error on spawn

WORKER_SCRIPT = _find_worker_script()

# ---------------------------------------------------------------------------
# Frame I/O
# ---------------------------------------------------------------------------


def recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    buf = bytearray()
    while len(buf) < nbytes:
        chunk = sock.recv(nbytes - len(buf))
        if not chunk:
            raise ConnectionError("Control channel closed")
        buf.extend(chunk)
    return bytes(buf)


def recv_command(sock: socket.socket) -> dict:
    header = recv_exact(sock, HEADER_SIZE)
    json_len, bin_len, msg_type = struct.unpack_from("<IIB", header)
    payload = recv_exact(sock, json_len + bin_len)
    return json.loads(payload[:json_len])


def send_response(sock: socket.socket, response: dict):
    json_bytes = json.dumps(response, separators=(",", ":")).encode("utf-8")
    header = struct.pack("<IIB", len(json_bytes), 0, MSG_TYPE_RESPONSE)
    sock.sendall(header + json_bytes)


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class Supervisor:
    def __init__(
        self,
        control_sock: socket.socket,
        poweroff_on_disconnect: bool = True,
        auth_secret: bytes | None = None,
    ):
        self.control_sock = control_sock
        self.workers: dict[int, subprocess.Popen] = {}
        self.execs: dict[int, dict] = {}
        self.execs_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.worker_cgroups: dict[int, str] = {}
        self.auth_secret = (
            auth_secret
            if auth_secret is not None
            else os.environ.get("SWIFTPYTHON_SUPERVISOR_SECRET", "").encode("utf-8")
        )
        self.auth_nonce = os.urandom(32).hex()
        self.authenticated = not bool(self.auth_secret)
        self.poweroff_on_disconnect = poweroff_on_disconnect
        self.parent_disconnect_timeout = float(os.environ.get("SWIFTPYTHON_PARENT_DISCONNECT_TIMEOUT", "5"))
        self.guest_sudo_mode = "none"
        self.cpu_quota_percent = 100
        self.max_open_files = 1024
        self.exec_user = "user"
        self.exec_home = "/home/user"
        self.running = True
        self.relisten_on_disconnect = False

    def _send_response(self, response: dict):
        with self.send_lock:
            send_response(self.control_sock, response)

    def run(self) -> bool:
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, self._handle_termination_signal)
        signal.signal(signal.SIGINT, self._handle_termination_signal)
        print(f"[supervisor] Connected to host, waiting for commands...", file=sys.stderr, flush=True)

        while self.running:
            try:
                cmd = recv_command(self.control_sock)
                response = self._handle_command(cmd)
                if response is not None:
                    self._send_response(response)
                if self.relisten_on_disconnect:
                    print("[supervisor] Snapshot prep complete, returning to listen",
                          file=sys.stderr, flush=True)
                    break
            except ConnectionError:
                print("[supervisor] Control channel closed, shutting down", file=sys.stderr, flush=True)
                if self.poweroff_on_disconnect:
                    threading.Thread(target=self._poweroff_after_parent_disconnect, daemon=True).start()
                break
            except Exception as e:
                print(f"[supervisor] Error: {e}", file=sys.stderr, flush=True)
                try:
                    self._send_response(ERROR(message=str(e)))
                except Exception:
                    break

        if self.relisten_on_disconnect:
            return True
        self._shutdown_all()
        return False

    @classmethod
    def _command_dispatch(cls) -> dict[str, str]:
        """Map command name -> handler attribute, built from the registrations.

        Derived by scanning for the marker `@command` leaves on each method, so
        a handler that is registered but unreachable, or reachable but
        unregistered, cannot exist.
        """
        table = cls.__dict__.get("_dispatch_cache")
        if table is None:
            table = {}
            for attr_name in dir(cls):
                attr = getattr(cls, attr_name, None)
                registered = getattr(attr, "_swiftpython_command", None)
                if registered is not None:
                    table[registered] = attr_name
            missing = {spec.name for spec in registered_commands()} - set(table)
            if missing:
                raise RuntimeError(f"Commands registered without a handler: {sorted(missing)}")
            cls._dispatch_cache = table
        return table

    def _handle_command(self, cmd: dict) -> dict | None:
        cmd_name = next(iter(cmd))
        cmd_data = cmd[cmd_name]

        spec = command_spec(cmd_name)
        if spec is None:
            return ERROR(message=f"Unknown command: {cmd_name}")

        if spec.requires_auth and not self.authenticated:
            if spec.channel_keyed and isinstance(cmd_data, dict) and "channelID" in cmd_data:
                return EXEC_ERROR(
                    channelID=int(cmd_data["channelID"]),
                    code=ExecErrorCode.INTERNAL_ERROR.value,
                    message="not authenticated",
                )
            return AUTH_FAILED(reason="not authenticated")

        handler = getattr(self, self._command_dispatch()[cmd_name])
        return handler(cmd_data)

    @command("describe")
    def _describe(self, data: dict) -> dict:
        """Answer with the live registries.

        The response is built by iterating the registries in
        `swiftpython_protocol`. Adding a frame there makes it appear here
        without this method changing — that property is the whole point, and a
        literal name anywhere in this method would defeat it.
        """
        emits = data.get("emits") if isinstance(data, dict) else None
        self._report_host_emit_skew(emits or [])
        return DESCRIBED(
            **declaration(
                worker_capabilities=duplex_capability_declaration(
                    CURRENT_PROTOCOL_VERSION,
                    "duplex.vsock.v1",
                ),
                guest_artifact_sha256=_guest_artifact_sha256(),
            )
        )

    def _report_host_emit_skew(self, host_emits: list) -> None:
        """Log the guest's view of the skew the host reports from its side.

        Gives `describe`'s `emits` field a consumer. Without one it would be a
        payload travelling the wire that nothing reads — the precise defect this
        handshake exists to remove, and the shape the old `supervisorVersion`
        had. It lands in the guest's own stderr, which is often the only log
        readable when a VM will not come up.
        """
        unsupported = sorted(set(host_emits) - {spec.name for spec in registered_commands()})
        if unsupported:
            print(
                "[supervisor] host may emit commands this guest does not serve: "
                + ", ".join(unsupported),
                file=sys.stderr,
                flush=True,
            )

    @command("configure")
    def _configure(self, data: dict) -> dict:
        sudo_mode = str(data.get("guestSudoMode", "none"))
        if sudo_mode not in ("none", "interactive", "nopasswd"):
            return ERROR(message=f"Invalid guestSudoMode: {sudo_mode}")

        cpu_quota = int(data.get("cpuQuotaPercent", 100))
        max_open = int(data.get("maxOpenFilesPerProcess", 1024))
        if cpu_quota < 0 or cpu_quota > 100:
            return ERROR(message="cpuQuotaPercent must be between 0 and 100")
        if max_open <= 0:
            return ERROR(message="maxOpenFilesPerProcess must be positive")

        self.guest_sudo_mode = sudo_mode
        self.cpu_quota_percent = cpu_quota
        self.max_open_files = max_open
        self.exec_user = str(data.get("execUser", "user"))
        self.exec_home = str(data.get("execHome", f"/home/{self.exec_user}"))
        self.parent_disconnect_timeout = float(
            data.get("parentDisconnectTimeout", self.parent_disconnect_timeout)
        )

        try:
            self._apply_sudo_policy()
        except Exception as exc:
            return ERROR(message=f"Failed to apply sudo policy: {exc}")

        return CONFIGURED(
            guestSudoMode=self.guest_sudo_mode,
            cpuQuotaPercent=self.cpu_quota_percent,
            maxOpenFilesPerProcess=self.max_open_files,
            execUser=self.exec_user,
        )

    def _apply_sudo_policy(self):
        sudoers_path = "/etc/sudoers.d/swiftpython-user"
        if os.geteuid() != 0:
            if self.guest_sudo_mode == "nopasswd":
                raise RuntimeError("nopasswd sudo mode requires a root supervisor")
            return
        try:
            if self.guest_sudo_mode == "nopasswd":
                with open(sudoers_path, "w", encoding="utf-8") as f:
                    f.write(f"{self.exec_user} ALL=(ALL) NOPASSWD:ALL\n")
                os.chmod(sudoers_path, 0o440)
            else:
                try:
                    os.unlink(sudoers_path)
                except FileNotFoundError:
                    pass
        except PermissionError:
            raise RuntimeError("supervisor must run as root to manage sudo policy")

    def _command_uses_sudo(self, command: str) -> bool:
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = command.replace(";", " ").replace("&&", " ").replace("||", " ").split()
        return any(token == "sudo" or token.endswith("/sudo") for token in tokens)

    def _exec_env(self) -> dict:
        env = os.environ.copy()
        env.update({
            "HOME": self.exec_home,
            "USER": self.exec_user,
            "LOGNAME": self.exec_user,
        })
        return env

    def _demote_to_exec_user(self):
        pw = pwd.getpwnam(self.exec_user)
        if os.geteuid() != 0 and pw.pw_uid == os.geteuid():
            return
        os.setgid(pw.pw_gid)
        os.initgroups(self.exec_user, pw.pw_gid)
        os.setuid(pw.pw_uid)

    def _cgroup_base(self) -> str:
        return "/sys/fs/cgroup/swiftpython"

    def _enable_cpu_controller(self, path: str):
        subtree = os.path.join(path, "cgroup.subtree_control")
        if os.path.exists(subtree):
            try:
                with open(subtree, "w", encoding="utf-8") as f:
                    f.write("+cpu\n")
            except OSError:
                pass

    def _create_cpu_cgroup(self, name: str) -> str | None:
        if self.cpu_quota_percent >= 100:
            return None
        root = "/sys/fs/cgroup"
        if not os.path.exists(os.path.join(root, "cgroup.controllers")):
            raise RuntimeError("cgroup v2 is required for cpuQuotaPercent")
        self._enable_cpu_controller(root)
        base = self._cgroup_base()
        os.makedirs(base, exist_ok=True)
        self._enable_cpu_controller(base)
        path = os.path.join(base, name)
        os.makedirs(path, exist_ok=True)
        quota = max(1, int(100000 * self.cpu_quota_percent / 100))
        with open(os.path.join(path, "cpu.max"), "w", encoding="utf-8") as f:
            f.write(f"{quota} 100000\n")
        return path

    def _remove_cgroup(self, path: str | None):
        if not path:
            return
        try:
            os.rmdir(path)
        except OSError:
            pass

    def _make_preexec(self, cgroup_path: str | None):
        def preexec():
            resource.setrlimit(resource.RLIMIT_NOFILE, (self.max_open_files, self.max_open_files))
            if cgroup_path:
                with open(os.path.join(cgroup_path, "cgroup.procs"), "w", encoding="utf-8") as f:
                    f.write(str(os.getpid()))
            self._demote_to_exec_user()
        return preexec

    @command("auth_challenge", requires_auth=False)
    def _auth_challenge(self, data: dict) -> dict:
        return AUTH_CHALLENGE(nonce=self.auth_nonce)

    @command("auth_response", requires_auth=False)
    def _auth_response(self, data: dict) -> dict:
        if not self.auth_secret:
            self.authenticated = True
            return AUTH_OK()

        client_version = int(data.get("clientVersion", 0))
        supplied = data.get("hmac", "")
        message = f"{self.auth_nonce}:{client_version}".encode("utf-8")
        expected = hmac.new(self.auth_secret, message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, supplied):
            self.authenticated = True
            return AUTH_OK()
        return AUTH_FAILED(reason="bad hmac")

    @command("rotate_auth_secret")
    def _rotate_auth_secret(self, data: dict) -> dict:
        encoded = data.get("secretBase64", "")
        try:
            new_secret = base64.b64decode(encoded, validate=True)
        except Exception:
            return ERROR(message="rotate_auth_secret requires valid base64 secret")
        if not new_secret:
            return ERROR(message="rotate_auth_secret requires a non-empty secret")
        self.auth_secret = new_secret
        self.auth_nonce = os.urandom(32).hex()
        self.authenticated = False
        return AUTH_ROTATED()

    @command("spawn", open_fields=("ipcConfig",))
    def _spawn_worker(self, data: dict) -> dict:
        worker_id = data["id"]
        vsock_port = data["port"]
        side_port = data.get("sidePort")
        ipc_config = data.get("ipcConfig", {})
        ipc_config_json = json.dumps(ipc_config)

        if worker_id in self.workers:
            proc = self.workers[worker_id]
            if proc.poll() is None:
                return ERROR(message=f"Worker {worker_id} already running (pid {proc.pid})")
            del self.workers[worker_id]
            self._remove_cgroup(self.worker_cgroups.pop(worker_id, None))

        cmd = [
            sys.executable,
            WORKER_SCRIPT,
            "--vsock-listen",
            str(worker_id),
            str(vsock_port),
            ipc_config_json,
        ]
        if side_port is not None:
            cmd.append(str(side_port))

        cgroup_path = None
        try:
            cgroup_path = self._create_cpu_cgroup(f"worker-{worker_id}")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=self._exec_env(),
                preexec_fn=self._make_preexec(cgroup_path),
            )
        except Exception as exc:
            self._remove_cgroup(cgroup_path)
            error: dict = {"message": f"Failed to spawn worker {worker_id}: {exc}"}
            if self.cpu_quota_percent < 100:
                error.update({
                    "code": ExecErrorCode.QUOTA_EXCEEDED.value,
                    "resource": QuotaResource.CPU.value,
                    "limit": self.cpu_quota_percent,
                    "observed": 100,
                })
            return ERROR(**error)
        self.workers[worker_id] = proc
        if cgroup_path:
            self.worker_cgroups[worker_id] = cgroup_path
        port_info = f"main={vsock_port}" + (f" side={side_port}" if side_port else "")
        print(f"[supervisor] Spawned worker {worker_id} (pid {proc.pid}) on vsock {port_info}",
              file=sys.stderr, flush=True)
        return SPAWNED(id=worker_id, pid=proc.pid)

    @command("kill")
    def _kill_worker(self, data: dict) -> dict:
        worker_id = data["id"]
        proc = self.workers.get(worker_id)
        if proc is None:
            return ERROR(message=f"Worker {worker_id} not found")

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)

        del self.workers[worker_id]
        self._remove_cgroup(self.worker_cgroups.pop(worker_id, None))
        print(f"[supervisor] Killed worker {worker_id}", file=sys.stderr, flush=True)
        return KILLED(id=worker_id)

    @command("abort")
    def _abort_worker(self, data: dict) -> dict:
        worker_id = data["id"]
        proc = self.workers.get(worker_id)
        if proc is None:
            return ERROR(message=f"Worker {worker_id} not found")
        if proc.poll() is not None:
            return ERROR(message=f"Worker {worker_id} already exited")

        os.kill(proc.pid, signal.SIGUSR1)
        return ABORTED(id=worker_id)

    @command("status")
    def _get_status(self, data: dict) -> dict:
        workers_status = {}
        for wid, proc in self.workers.items():
            workers_status[str(wid)] = {
                "pid": proc.pid,
                "alive": proc.poll() is None,
            }
        with self.execs_lock:
            exec_status = {
                str(cid): {"pid": entry["proc"].pid, "pty": bool(entry.get("pty"))}
                for cid, entry in self.execs.items()
                if entry["proc"].poll() is None
            }
        return STATUS(workers=workers_status, execs=exec_status)

    @command("idle_status")
    def _idle_status(self, data: dict | None = None) -> dict:
        live_workers = 0
        for proc in self.workers.values():
            if proc.poll() is None:
                live_workers += 1
        with self.execs_lock:
            live_execs = sum(1 for entry in self.execs.values() if entry["proc"].poll() is None)
        payload = {
            "idle": live_workers == 0 and live_execs == 0,
            "workers": live_workers,
            "execs": live_execs,
        }
        # Open-region backstop: keys emitted outside the declaration on paths
        # that actually executed. Absent when nothing was observed, so a healthy
        # guest reports nothing rather than an empty reassurance.
        undeclared = observed_undeclared_keys()
        if undeclared:
            payload["observedUndeclaredKeys"] = undeclared
        return IDLE_STATUS(**payload)

    @command("prepare_snapshot")
    def _prepare_snapshot(self, data: dict | None = None) -> dict:
        idle = self._idle_status()["idle_status"]
        if not idle["idle"]:
            return ERROR(
                message=(
                    f"Snapshot requires idle supervisor: "
                    f"workers={idle['workers']} execs={idle['execs']}"
                )
            )
        self.poweroff_on_disconnect = False
        self.relisten_on_disconnect = True
        return SNAPSHOT_READY(workers=idle["workers"], execs=idle["execs"])

    def _send_exec_chunk(self, channel_id: int, stream: str, data: bytes):
        if not data:
            return
        constructor = EXEC_STDOUT if stream == "stdout" else EXEC_STDERR
        self._send_response(constructor(
            channelID=channel_id,
            bytes=base64.b64encode(data).decode("ascii"),
        ))

    def _send_exec_result(self, channel_id: int, proc: subprocess.Popen, started: float, truncated: bool = False):
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._send_response(EXEC_RESULT(
            channelID=channel_id,
            exitCode=int(proc.returncode if proc.returncode is not None else -1),
            elapsedMs=elapsed_ms,
            truncated=truncated,
        ))

    def _send_exec_error(self, channel_id: int, code: ExecErrorCode, message: str, **extra):
        self._send_response(EXEC_ERROR(
            channelID=channel_id,
            code=code.value,
            message=message,
            **extra,
        ))

    def _send_output_limit_exceeded(self, channel_id: int, observed: int, limit: int):
        self._send_exec_error(
            channel_id,
            ExecErrorCode.RESOURCE_ERROR,
            "execOutputLimitExceeded",
            observed=observed,
            limit=limit,
        )

    def _kill_exec_process(self, proc: subprocess.Popen):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass

    def _set_winsize(self, fd: int, size: dict):
        rows = int(size.get("rows", 24))
        cols = int(size.get("columns", size.get("cols", 80)))
        xpixels = int(size.get("xPixels", 0))
        ypixels = int(size.get("yPixels", 0))
        packed = struct.pack("HHHH", rows, cols, xpixels, ypixels)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)

    @command("exec", replies_on_channel=True, channel_keyed=True)
    def _exec(self, data: dict) -> None:
        self._start_exec(data, use_pty=False)
        return None

    @command("exec_stream", replies_on_channel=True, channel_keyed=True)
    def _exec_stream(self, data: dict) -> None:
        self._start_exec(data, use_pty=False)
        return None

    @command("exec_pty", replies_on_channel=True, channel_keyed=True)
    def _exec_pty(self, data: dict) -> None:
        self._start_exec(data, use_pty=True)
        return None

    def _start_exec(self, data: dict, use_pty: bool):
        threading.Thread(target=self._run_exec, args=(data, use_pty), daemon=True).start()

    def _run_exec(self, data: dict, use_pty: bool):
        channel_id = int(data["channelID"])
        command = data["command"]
        timeout = float(data.get("timeout", 120.0))
        max_output = int(data.get("maxOutputBytes", 64 * 1024 * 1024))
        stdin_bytes = base64.b64decode(data.get("stdinBase64", "")) if data.get("stdinBase64") else None
        interactive = bool(data.get("interactive", False))
        started = time.monotonic()
        proc = None
        selector = None
        master_fd = None
        cgroup_path = None

        try:
            if self.guest_sudo_mode == "none" and self._command_uses_sudo(command):
                self._send_exec_error(channel_id, ExecErrorCode.SUDO_DISABLED, command)
                return

            cgroup_path = self._create_cpu_cgroup(f"exec-{channel_id}")
            preexec = self._make_preexec(cgroup_path)
            if use_pty:
                master_fd, slave_fd = pty.openpty()
                if "initialSize" in data:
                    self._set_winsize(master_fd, data["initialSize"])
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    start_new_session=True,
                    close_fds=True,
                    cwd=self.exec_home,
                    env=self._exec_env(),
                    preexec_fn=preexec,
                )
                os.close(slave_fd)
                os.set_blocking(master_fd, False)
                selector = selectors.DefaultSelector()
                selector.register(master_fd, selectors.EVENT_READ, "stdout")
            else:
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    cwd=self.exec_home,
                    env=self._exec_env(),
                    preexec_fn=preexec,
                )
                if stdin_bytes is not None and proc.stdin is not None:
                    proc.stdin.write(stdin_bytes)
                if proc.stdin is not None and not interactive:
                    proc.stdin.close()
                os.set_blocking(proc.stdout.fileno(), False)
                os.set_blocking(proc.stderr.fileno(), False)
                selector = selectors.DefaultSelector()
                selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
                selector.register(proc.stderr, selectors.EVENT_READ, "stderr")

            with self.execs_lock:
                self.execs[channel_id] = {
                    "proc": proc,
                    "pty": use_pty,
                    "master_fd": master_fd,
                    "stdin": proc.stdin if not use_pty else None,
                }
            self._send_response(EXEC_STARTED(channelID=channel_id))

            observed = 0
            deadline = started + timeout
            while True:
                if time.monotonic() > deadline:
                    self._kill_exec_process(proc)
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    self._send_response(EXEC_TIMEOUT(channelID=channel_id, elapsedMs=elapsed_ms))
                    return

                events = selector.select(timeout=0.05)
                for key, _ in events:
                    stream = key.data
                    try:
                        chunk = os.read(key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        chunk = b""
                    if not chunk:
                        try:
                            selector.unregister(key.fileobj)
                        except Exception:
                            pass
                        continue
                    observed += len(chunk)
                    if observed > max_output:
                        self._kill_exec_process(proc)
                        self._send_output_limit_exceeded(channel_id, observed, max_output)
                        return
                    self._send_exec_chunk(channel_id, stream, chunk)

                if proc.poll() is not None:
                    for key in list(selector.get_map().values()):
                        stream = key.data
                        while True:
                            try:
                                chunk = os.read(
                                    key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno(),
                                    65536,
                                )
                            except BlockingIOError:
                                break
                            except OSError:
                                chunk = b""
                            if not chunk:
                                break
                            observed += len(chunk)
                            if observed > max_output:
                                self._kill_exec_process(proc)
                                self._send_output_limit_exceeded(channel_id, observed, max_output)
                                return
                            self._send_exec_chunk(channel_id, stream, chunk)
                        try:
                            selector.unregister(key.fileobj)
                        except Exception:
                            pass
                    break

            proc.wait(timeout=0)
            self._send_exec_result(channel_id, proc, started)
        except Exception as e:
            if proc is not None:
                self._kill_exec_process(proc)
            message = str(e)
            if "cgroup" in message or "cpuQuotaPercent" in message:
                self._send_exec_error(
                    channel_id,
                    ExecErrorCode.QUOTA_EXCEEDED,
                    "cpuQuotaUnavailable",
                    resource=QuotaResource.CPU.value,
                    limit=self.cpu_quota_percent,
                    observed=100,
                )
            elif "RLIMIT_NOFILE" in message or "setrlimit" in message:
                self._send_exec_error(
                    channel_id,
                    ExecErrorCode.QUOTA_EXCEEDED,
                    "openFileQuotaUnavailable",
                    resource=QuotaResource.OPEN_FILES.value,
                    limit=self.max_open_files,
                    observed=self.max_open_files,
                )
            else:
                self._send_exec_error(channel_id, ExecErrorCode.INTERNAL_ERROR, message)
        finally:
            with self.execs_lock:
                self.execs.pop(channel_id, None)
            self._remove_cgroup(cgroup_path)
            if selector is not None:
                selector.close()
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

    @command("exec_stdin", channel_keyed=True)
    def _exec_stdin(self, data: dict) -> dict:
        channel_id = int(data["channelID"])
        payload = base64.b64decode(data.get("bytes", "")) if data.get("bytes") else b""
        eof = bool(data.get("eof", False))
        deadline = time.monotonic() + 1.0
        entry = None
        while time.monotonic() < deadline:
            with self.execs_lock:
                entry = self.execs.get(channel_id)
            if entry:
                break
            time.sleep(0.01)
        if not entry:
            if eof and not payload:
                return EXEC_STDIN_ACK(channelID=channel_id)
            return self._stdin_unavailable(channel_id)
        if entry.get("pty") and entry.get("master_fd") is not None:
            if payload:
                os.write(entry["master_fd"], payload)
            return EXEC_STDIN_ACK(channelID=channel_id)

        stdin = entry.get("stdin")
        if stdin is None:
            return self._stdin_unavailable(channel_id)
        if payload:
            stdin.write(payload)
            stdin.flush()
        if eof:
            stdin.close()
        return EXEC_STDIN_ACK(channelID=channel_id)

    def _stdin_unavailable(self, channel_id: int) -> dict:
        """The exec this stdin write targets is gone.

        Declared droppable via `EXEC_ERROR.droppable_codes` so the host can
        discard it quietly when the channel has already been torn down, instead
        of recognising it by matching this message's English text.
        """
        return EXEC_ERROR(
            channelID=channel_id,
            code=ExecErrorCode.INTERNAL_ERROR.value,
            message="stdin unavailable",
        )

    def _poweroff_after_parent_disconnect(self):
        time.sleep(self.parent_disconnect_timeout)
        self._shutdown_all()
        try:
            subprocess.run(["/sbin/poweroff"], timeout=2.0)
        except Exception:
            try:
                subprocess.run(["poweroff"], timeout=2.0)
            except Exception:
                os._exit(0)

    def _handle_termination_signal(self, signum, frame):
        self.running = False
        self._force_kill_children()
        try:
            self.control_sock.close()
        except Exception:
            pass
        os._exit(128 + int(signum))

    def _force_kill_children(self):
        for entry in list(self.execs.values()):
            proc = entry.get("proc")
            if proc is None or proc.poll() is not None:
                continue
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        for proc in list(self.workers.values()):
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

    @command("exec_signal", channel_keyed=True)
    def _exec_signal(self, data: dict) -> dict:
        channel_id = int(data["channelID"])
        with self.execs_lock:
            entry = self.execs.get(channel_id)
        if not entry:
            return EXEC_ERROR(
                channelID=channel_id,
                code=ExecErrorCode.INTERNAL_ERROR.value,
                message="exec channel not found",
            )
        if data.get("terminalSize") and entry.get("master_fd") is not None:
            self._set_winsize(entry["master_fd"], data["terminalSize"])
        signal_number = int(data.get("signal", 0))
        if signal_number:
            try:
                os.killpg(entry["proc"].pid, signal_number)
            except ProcessLookupError:
                pass
        return EXEC_SIGNAL_ACK(channelID=channel_id)

    @command("shutdown")
    def _shutdown(self, data: dict) -> dict:
        self.running = False
        return SHUTTING_DOWN()

    def _shutdown_all(self):
        with self.execs_lock:
            exec_entries = list(self.execs.items())
            self.execs.clear()
        for _, entry in exec_entries:
            self._kill_exec_process(entry["proc"])

        for wid, proc in list(self.workers.items()):
            if proc.poll() is None:
                proc.terminate()
        deadline = time.monotonic() + 3.0
        for wid, proc in list(self.workers.items()):
            remaining = max(0, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._remove_cgroup(self.worker_cgroups.pop(wid, None))
        self.workers.clear()
        print("[supervisor] All workers terminated", file=sys.stderr, flush=True)


# Resolve the dispatch table at import so a command registered without a
# handler stops the guest at startup, with a traceback in the supervisor's own
# log. Left to the first command it would be raised inside the run loop's
# catch-all, which answers the host with a generic `error` frame and leaves a
# silently degraded supervisor running for the life of the connection.
Supervisor._command_dispatch()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    AF_VSOCK = 40  # macOS and Linux AF_VSOCK

    if len(sys.argv) >= 3 and sys.argv[1] == "--uds":
        # UDS mode for local testing: --uds <socket_path>
        auth_secret = os.environ.get("SWIFTPYTHON_SUPERVISOR_SECRET", "").encode("utf-8")
        try:
            while True:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(sys.argv[2])
                supervisor = Supervisor(
                    sock,
                    poweroff_on_disconnect=False,
                    auth_secret=auth_secret,
                )
                try:
                    relisten = supervisor.run()
                except KeyboardInterrupt:
                    relisten = False
                finally:
                    auth_secret = supervisor.auth_secret
                    try:
                        sock.close()
                    except Exception:
                        pass
                if not relisten:
                    break
        finally:
            os._exit(0)
    else:
        # Listen on vsock for host connection.
        # The host uses VZVirtioSocketDevice.connect(toPort:) to reach us.
        # This is the proven pattern from sirius-agent.
        port = int(sys.argv[1]) if len(sys.argv) >= 2 else CONTROL_PORT
        server = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((VSOCK_CID_ANY, port))
        server.listen(1)
        print(f"[supervisor] Listening on vsock port {port}",
              file=sys.stderr, flush=True)
        auth_secret = os.environ.get("SWIFTPYTHON_SUPERVISOR_SECRET", "").encode("utf-8")
        try:
            while True:
                sock, _ = server.accept()
                print(f"[supervisor] Host connected on vsock port {port}",
                      file=sys.stderr, flush=True)
                supervisor = Supervisor(
                    sock,
                    poweroff_on_disconnect=True,
                    auth_secret=auth_secret,
                )
                try:
                    relisten = supervisor.run()
                except KeyboardInterrupt:
                    relisten = False
                finally:
                    auth_secret = supervisor.auth_secret
                    try:
                        sock.close()
                    except Exception:
                        pass
                if not relisten:
                    break
        finally:
            server.close()
            os._exit(0)


if __name__ == "__main__":
    main()
