#!/usr/bin/env python3
"""SwiftPython VM Supervisor — Manages worker processes inside the macOS guest VM.

Runs as a LaunchAgent inside the VM. Connects to the host via AF_VSOCK on port 0
(control channel) and spawns/kills Python worker processes on command.

Control channel protocol (JSON over length-prefixed frames, same header as MessageFrame):
    Request:  {"spawn": {"id": 0, "port": 1, "ipcConfig": {...}}}
    Response: {"spawned": {"id": 0, "pid": 12345}}

    Request:  {"kill": {"id": 0}}
    Response: {"killed": {"id": 0}}

    Request:  {"abort": {"id": 0}}
    Response: {"aborted": {"id": 0}}

    Request:  {"status": {}}
    Response: {"status": {"workers": {0: {"pid": 12345, "alive": true}, ...}}}

    Request:  {"shutdown": {}}
    Response: {"shutting_down": {}}
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
    def __init__(self, control_sock: socket.socket, poweroff_on_disconnect: bool = True):
        self.control_sock = control_sock
        self.workers: dict[int, subprocess.Popen] = {}
        self.execs: dict[int, dict] = {}
        self.execs_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.worker_cgroups: dict[int, str] = {}
        self.auth_secret = os.environ.get("SWIFTPYTHON_SUPERVISOR_SECRET", "").encode("utf-8")
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

    def _send_response(self, response: dict):
        with self.send_lock:
            send_response(self.control_sock, response)

    def run(self):
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
            except ConnectionError:
                print("[supervisor] Control channel closed, shutting down", file=sys.stderr, flush=True)
                if self.poweroff_on_disconnect:
                    threading.Thread(target=self._poweroff_after_parent_disconnect, daemon=True).start()
                break
            except Exception as e:
                print(f"[supervisor] Error: {e}", file=sys.stderr, flush=True)
                try:
                    self._send_response({"error": {"message": str(e)}})
                except Exception:
                    break

        self._shutdown_all()

    def _handle_command(self, cmd: dict) -> dict:
        cmd_name = next(iter(cmd))
        cmd_data = cmd[cmd_name]

        if cmd_name == "auth_challenge":
            return self._auth_challenge(cmd_data)
        if cmd_name == "auth_response":
            return self._auth_response(cmd_data)
        if not self.authenticated:
            if isinstance(cmd_data, dict) and "channelID" in cmd_data and cmd_name.startswith("exec"):
                return {
                    "exec_error": {
                        "channelID": int(cmd_data["channelID"]),
                        "code": "internalError",
                        "message": "not authenticated",
                    }
            }
            return {"auth_failed": {"reason": "not authenticated"}}

        if cmd_name == "configure":
            return self._configure(cmd_data)
        if cmd_name == "spawn":
            return self._spawn_worker(cmd_data)
        if cmd_name == "kill":
            return self._kill_worker(cmd_data)
        if cmd_name == "abort":
            return self._abort_worker(cmd_data)
        if cmd_name in ("exec", "exec_stream"):
            threading.Thread(target=self._run_exec, args=(cmd_data, False), daemon=True).start()
            return None
        if cmd_name == "exec_pty":
            threading.Thread(target=self._run_exec, args=(cmd_data, True), daemon=True).start()
            return None
        if cmd_name == "exec_stdin":
            return self._exec_stdin(cmd_data)
        if cmd_name == "exec_signal":
            return self._exec_signal(cmd_data)
        if cmd_name == "status":
            return self._get_status()
        if cmd_name == "shutdown":
            self.running = False
            return {"shutting_down": {}}

        return {"error": {"message": f"Unknown command: {cmd_name}"}}

    def _configure(self, data: dict) -> dict:
        sudo_mode = str(data.get("guestSudoMode", "none"))
        if sudo_mode not in ("none", "interactive", "nopasswd"):
            return {"error": {"message": f"Invalid guestSudoMode: {sudo_mode}"}}

        cpu_quota = int(data.get("cpuQuotaPercent", 100))
        max_open = int(data.get("maxOpenFilesPerProcess", 1024))
        if cpu_quota < 0 or cpu_quota > 100:
            return {"error": {"message": "cpuQuotaPercent must be between 0 and 100"}}
        if max_open <= 0:
            return {"error": {"message": "maxOpenFilesPerProcess must be positive"}}

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
            return {"error": {"message": f"Failed to apply sudo policy: {exc}"}}

        return {
            "configured": {
                "guestSudoMode": self.guest_sudo_mode,
                "cpuQuotaPercent": self.cpu_quota_percent,
                "maxOpenFilesPerProcess": self.max_open_files,
                "execUser": self.exec_user,
            }
        }

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

    def _auth_challenge(self, data: dict) -> dict:
        return {
            "auth_challenge": {
                "nonce": self.auth_nonce,
                "protocolVersion": 4,
                "supervisorVersion": 2,
            }
        }

    def _auth_response(self, data: dict) -> dict:
        if not self.auth_secret:
            self.authenticated = True
            return {"auth_ok": {"supervisorVersion": 2, "protocolVersion": 4}}

        client_version = int(data.get("clientVersion", 0))
        supplied = data.get("hmac", "")
        message = f"{self.auth_nonce}:{client_version}".encode("utf-8")
        expected = hmac.new(self.auth_secret, message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, supplied):
            self.authenticated = True
            return {"auth_ok": {"supervisorVersion": 2, "protocolVersion": 4}}
        return {"auth_failed": {"reason": "bad hmac"}}

    def _spawn_worker(self, data: dict) -> dict:
        worker_id = data["id"]
        vsock_port = data["port"]
        side_port = data.get("sidePort")
        ipc_config = data.get("ipcConfig", {})
        ipc_config_json = json.dumps(ipc_config)

        if worker_id in self.workers:
            proc = self.workers[worker_id]
            if proc.poll() is None:
                return {"error": {"message": f"Worker {worker_id} already running (pid {proc.pid})"}}
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
            error = {"message": f"Failed to spawn worker {worker_id}: {exc}"}
            if self.cpu_quota_percent < 100:
                error.update({
                    "code": "quotaExceeded",
                    "resource": "cpu",
                    "limit": self.cpu_quota_percent,
                    "observed": 100,
                })
            return {"error": error}
        self.workers[worker_id] = proc
        if cgroup_path:
            self.worker_cgroups[worker_id] = cgroup_path
        port_info = f"main={vsock_port}" + (f" side={side_port}" if side_port else "")
        print(f"[supervisor] Spawned worker {worker_id} (pid {proc.pid}) on vsock {port_info}",
              file=sys.stderr, flush=True)
        return {"spawned": {"id": worker_id, "pid": proc.pid}}

    def _kill_worker(self, data: dict) -> dict:
        worker_id = data["id"]
        proc = self.workers.get(worker_id)
        if proc is None:
            return {"error": {"message": f"Worker {worker_id} not found"}}

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
        return {"killed": {"id": worker_id}}

    def _abort_worker(self, data: dict) -> dict:
        worker_id = data["id"]
        proc = self.workers.get(worker_id)
        if proc is None:
            return {"error": {"message": f"Worker {worker_id} not found"}}
        if proc.poll() is not None:
            return {"error": {"message": f"Worker {worker_id} already exited"}}

        os.kill(proc.pid, signal.SIGUSR1)
        return {"aborted": {"id": worker_id}}

    def _get_status(self) -> dict:
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
        return {"status": {"workers": workers_status, "execs": exec_status}}

    def _send_exec_chunk(self, channel_id: int, stream: str, data: bytes):
        if not data:
            return
        key = "exec_stdout" if stream == "stdout" else "exec_stderr"
        self._send_response({
            key: {
                "channelID": channel_id,
                "bytes": base64.b64encode(data).decode("ascii"),
            }
        })

    def _send_exec_result(self, channel_id: int, proc: subprocess.Popen, started: float, truncated: bool = False):
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._send_response({
            "exec_result": {
                "channelID": channel_id,
                "exitCode": int(proc.returncode if proc.returncode is not None else -1),
                "elapsedMs": elapsed_ms,
                "truncated": truncated,
            }
        })

    def _send_exec_error(self, channel_id: int, code: str, message: str, **extra):
        payload = {
            "channelID": channel_id,
            "code": code,
            "message": message,
        }
        payload.update(extra)
        self._send_response({"exec_error": payload})

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
                self._send_response({
                    "exec_error": {
                        "channelID": channel_id,
                        "code": "sudoDisabled",
                        "message": command,
                    }
                })
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
            self._send_response({"exec_started": {"channelID": channel_id}})

            observed = 0
            deadline = started + timeout
            while True:
                if time.monotonic() > deadline:
                    self._kill_exec_process(proc)
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    self._send_response({"exec_timeout": {"channelID": channel_id, "elapsedMs": elapsed_ms}})
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
                        self._send_response({
                            "exec_error": {
                                "channelID": channel_id,
                                "code": "resourceError",
                                "message": "execOutputLimitExceeded",
                                "observed": observed,
                                "limit": max_output,
                            }
                        })
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
                                self._send_response({
                                    "exec_error": {
                                        "channelID": channel_id,
                                        "code": "resourceError",
                                        "message": "execOutputLimitExceeded",
                                        "observed": observed,
                                        "limit": max_output,
                                    }
                                })
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
                    "quotaExceeded",
                    "cpuQuotaUnavailable",
                    resource="cpu",
                    limit=self.cpu_quota_percent,
                    observed=100,
                )
            elif "RLIMIT_NOFILE" in message or "setrlimit" in message:
                self._send_exec_error(
                    channel_id,
                    "quotaExceeded",
                    "openFileQuotaUnavailable",
                    resource="openFiles",
                    limit=self.max_open_files,
                    observed=self.max_open_files,
                )
            else:
                self._send_exec_error(channel_id, "internalError", message)
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

    def _exec_stdin(self, data: dict) -> dict:
        channel_id = int(data["channelID"])
        deadline = time.monotonic() + 1.0
        entry = None
        while time.monotonic() < deadline:
            with self.execs_lock:
                entry = self.execs.get(channel_id)
            if entry:
                break
            time.sleep(0.01)
        if not entry:
            return {"exec_error": {"channelID": channel_id, "code": "internalError", "message": "stdin unavailable"}}
        payload = base64.b64decode(data.get("bytes", "")) if data.get("bytes") else b""
        if entry.get("pty") and entry.get("master_fd") is not None:
            if payload:
                os.write(entry["master_fd"], payload)
            return {"exec_stdin_ack": {"channelID": channel_id}}

        stdin = entry.get("stdin")
        if stdin is None:
            return {"exec_error": {"channelID": channel_id, "code": "internalError", "message": "stdin unavailable"}}
        if payload:
            stdin.write(payload)
            stdin.flush()
        if data.get("eof", False):
            stdin.close()
        return {"exec_stdin_ack": {"channelID": channel_id}}

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

    def _exec_signal(self, data: dict) -> dict:
        channel_id = int(data["channelID"])
        with self.execs_lock:
            entry = self.execs.get(channel_id)
        if not entry:
            return {"exec_error": {"channelID": channel_id, "code": "internalError", "message": "exec channel not found"}}
        if data.get("terminalSize") and entry.get("master_fd") is not None:
            self._set_winsize(entry["master_fd"], data["terminalSize"])
        signal_number = int(data.get("signal", 0))
        if signal_number:
            try:
                os.killpg(entry["proc"].pid, signal_number)
            except ProcessLookupError:
                pass
        return {"exec_signal_ack": {"channelID": channel_id}}

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    AF_VSOCK = 40  # macOS and Linux AF_VSOCK

    if len(sys.argv) >= 3 and sys.argv[1] == "--uds":
        # UDS mode for local testing: --uds <socket_path>
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sys.argv[2])
        poweroff_on_disconnect = False
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
        sock, _ = server.accept()
        server.close()
        print(f"[supervisor] Host connected on vsock port {port}",
              file=sys.stderr, flush=True)
        poweroff_on_disconnect = True

    supervisor = Supervisor(sock, poweroff_on_disconnect=poweroff_on_disconnect)
    try:
        supervisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        os._exit(0)


if __name__ == "__main__":
    main()
