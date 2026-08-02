"""Closed vocabulary for the SwiftPython supervisor control channel (guest side).

The supervisor is the *producer* of every frame that travels guest -> host, and
the *consumer* of every command that travels host -> guest. This module is where
that half of the vocabulary lives as a language construct rather than as string
literals scattered across emission sites.

Two registries are populated at import time:

* ``_FRAME_REGISTRY`` — one :class:`FrameSpec` per guest-produced frame kind.
  Frames are only constructible through the callable returned by :func:`frame`,
  which validates the payload against the declared field set. Adding a frame
  kind is therefore a registration, not a new dictionary display.
* ``_COMMAND_REGISTRY`` — one :class:`CommandSpec` per host-produced command the
  supervisor is willing to serve, populated by the ``@command`` decorator in
  ``swiftpython_supervisor.py``.

:func:`declaration` serialises both registries by iterating them. It contains no
list of names. The host builds its routing table from that response at connect
time, so there is no checked-in copy of this vocabulary anywhere on the host and
nothing that can drift against a baked guest image.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

# Supervisor control-channel protocol version. Independent of the worker wire
# protocol (`IPCConfiguration.currentProtocolVersion`, currently 6) — the two
# counters version separately and conflating them is a protocol error. Guests
# predating this module reported `supervisorVersion: 2` and did not answer
# `describe`; 3 is the first free value.
SUPERVISOR_PROTOCOL_VERSION = 3

# The worker wire protocol version this supervisor's workers speak. Reported
# alongside the supervisor version on the auth frames for host-side skew checks.
WORKER_PROTOCOL_VERSION = 6

# When set, an emission carrying a key outside the declared field set raises
# instead of being recorded. Enabled in CI guest builds.
STRICT_FRAMES_ENV = "SWIFTPYTHON_STRICT_FRAMES"


def strict_frames_enabled() -> bool:
    return os.environ.get(STRICT_FRAMES_ENV, "") not in ("", "0", "false", "False")


# ---------------------------------------------------------------------------
# Routing semantics — declared by the producer, never inferred by the consumer
# ---------------------------------------------------------------------------


class Routing(enum.Enum):
    """Where a frame goes on the host once it arrives."""

    #: Reply to the pending control request.
    CONTROL = "control"
    #: Keyed by ``payload["channelID"]``; the channel stays open.
    CHANNEL = "channel"
    #: Keyed by ``payload["channelID"]``; terminates the channel.
    CHANNEL_FINAL = "channel_final"


class ExecErrorCode(enum.Enum):
    """``exec_error.code`` values. Closed: adding one is a registration."""

    INTERNAL_ERROR = "internalError"
    RESOURCE_ERROR = "resourceError"
    SUDO_DISABLED = "sudoDisabled"
    QUOTA_EXCEEDED = "quotaExceeded"


class QuotaResource(enum.Enum):
    """``exec_error.resource`` values for :attr:`ExecErrorCode.QUOTA_EXCEEDED`."""

    CPU = "cpu"
    OPEN_FILES = "openFiles"
    MEMORY = "memory"
    DISK = "disk"


# ---------------------------------------------------------------------------
# Frame registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameSpec:
    """Declaration of one guest-produced frame kind."""

    name: str
    routing: Routing
    #: Keys that must be present on every emission.
    fields: tuple[str, ...] = ()
    #: Keys that may be present. Declared open regions only — see `open_region`.
    open_fields: tuple[str, ...] = ()
    #: Keys present on some emissions and absent on others, but still closed.
    optional_fields: tuple[str, ...] = ()
    #: Safe for the host to discard without logging when its channel is gone.
    droppable_when_unrouted: bool = False
    #: ``code`` values for which this frame is droppable when unrouted. Lets a
    #: late "the exec you are writing to is gone" error be dropped quietly
    #: without the host matching on the error's English message text.
    droppable_codes: tuple[str, ...] = ()

    @property
    def allowed_fields(self) -> frozenset[str]:
        return frozenset(self.fields + self.optional_fields + self.open_fields)

    def as_declaration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "routing": self.routing.value,
            "fields": list(self.fields),
            "optional": list(self.optional_fields),
            "open": list(self.open_fields),
            "droppable": self.droppable_when_unrouted,
            "droppableCodes": list(self.droppable_codes),
        }


class FrameError(TypeError):
    """Raised when an emission does not match its declaration."""


_FRAME_REGISTRY: dict[str, FrameSpec] = {}

# Bounded record of undeclared keys seen in a non-strict guest, reported back to
# the host so an open region that has silently grown is observable. Bounded so a
# runaway producer cannot grow it without limit.
_MAX_OBSERVED_UNDECLARED = 32
_observed_undeclared: set[str] = set()


class FrameConstructor:
    """Callable that builds one frame kind, validating against its declaration."""

    __slots__ = ("spec",)

    def __init__(self, spec: FrameSpec):
        self.spec = spec

    def __call__(self, **payload: Any) -> dict[str, Any]:
        supplied = frozenset(payload)
        missing = frozenset(self.spec.fields) - supplied
        if missing:
            raise FrameError(
                f"{self.spec.name} is missing declared field(s): {sorted(missing)}"
            )
        undeclared = supplied - self.spec.allowed_fields
        if undeclared:
            if strict_frames_enabled():
                raise FrameError(
                    f"{self.spec.name} emitted undeclared field(s): {sorted(undeclared)}"
                )
            for key in undeclared:
                if len(_observed_undeclared) < _MAX_OBSERVED_UNDECLARED:
                    _observed_undeclared.add(f"{self.spec.name}.{key}")
        return {self.spec.name: payload}


def frame(spec: FrameSpec) -> FrameConstructor:
    """Register ``spec`` and return the only way to construct that frame."""
    if spec.name in _FRAME_REGISTRY:
        raise FrameError(f"Frame {spec.name} is already registered")
    _FRAME_REGISTRY[spec.name] = spec
    return FrameConstructor(spec)


def registered_frames() -> tuple[FrameSpec, ...]:
    return tuple(_FRAME_REGISTRY[name] for name in sorted(_FRAME_REGISTRY))


def observed_undeclared_keys() -> list[str]:
    return sorted(_observed_undeclared)


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """Declaration of one host-produced command the supervisor serves."""

    name: str
    #: False for the auth handshake commands, which run before authentication.
    requires_auth: bool = True
    #: True when the command's result arrives as an asynchronous channel frame
    #: rather than as a control reply.
    replies_on_channel: bool = False
    #: True when the command carries a ``channelID``, so a pre-dispatch refusal
    #: must be reported as an ``exec_error`` on that channel rather than as a
    #: control frame the exec caller will never read.
    channel_keyed: bool = False
    #: Keys forwarded verbatim without the supervisor interpreting them.
    open_fields: tuple[str, ...] = ()

    def as_declaration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "requiresAuth": self.requires_auth,
            "repliesOnChannel": self.replies_on_channel,
            "channelKeyed": self.channel_keyed,
            "open": list(self.open_fields),
        }


_COMMAND_REGISTRY: dict[str, CommandSpec] = {}


def command(
    name: str,
    *,
    requires_auth: bool = True,
    replies_on_channel: bool = False,
    channel_keyed: bool = False,
    open_fields: tuple[str, ...] = (),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a supervisor method as the handler for command ``name``."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _COMMAND_REGISTRY:
            raise FrameError(f"Command {name} is already registered")
        _COMMAND_REGISTRY[name] = CommandSpec(
            name=name,
            requires_auth=requires_auth,
            replies_on_channel=replies_on_channel,
            channel_keyed=channel_keyed,
            open_fields=open_fields,
        )
        setattr(fn, "_swiftpython_command", name)
        return fn

    return decorate


def registered_commands() -> tuple[CommandSpec, ...]:
    return tuple(_COMMAND_REGISTRY[name] for name in sorted(_COMMAND_REGISTRY))


def command_spec(name: str) -> CommandSpec | None:
    return _COMMAND_REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Open regions (INV-V7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenRegion:
    """A payload region that is open by construction and cannot be closed."""

    name: str
    reason: str
    declared_keys: tuple[str, ...] = field(default=())

    def as_declaration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "reason": self.reason,
            "declaredKeys": list(self.declared_keys),
        }


OPEN_REGIONS: tuple[OpenRegion, ...] = (
    OpenRegion(
        name="spawn.ipcConfig",
        reason=(
            "Forwarded verbatim to the worker as argv; the supervisor is a conduit "
            "and must not need redeployment for every IPCConfiguration field."
        ),
    ),
    OpenRegion(
        name="exec_error.extra",
        reason=(
            "Per-code diagnostic extras; adding a quota kind must not be a "
            "wire-protocol event."
        ),
        declared_keys=("resource", "limit", "observed"),
    ),
)


# ---------------------------------------------------------------------------
# The declaration answered by `describe`
# ---------------------------------------------------------------------------


def declaration(
    extra_accepts: Iterable[str] = (),
    *,
    worker_capabilities: dict[str, Any] | None = None,
    guest_artifact_sha256: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Serialise the live registries.

    Every list below is produced by iterating a registry. Nothing here is a
    literal name — if it were, the host would be deriving from a third copy of
    the vocabulary instead of from the running producer.
    """
    accepts = sorted({spec.name for spec in registered_commands()} | set(extra_accepts))
    return {
        "supervisorProtocolVersion": SUPERVISOR_PROTOCOL_VERSION,
        "protocolVersion": WORKER_PROTOCOL_VERSION,
        "accepts": accepts,
        "commands": [spec.as_declaration() for spec in registered_commands()],
        "frames": [spec.as_declaration() for spec in registered_frames()],
        "errorCodes": [code.value for code in ExecErrorCode],
        "quotaResources": [resource.value for resource in QuotaResource],
        "openRegions": [region.as_declaration() for region in OPEN_REGIONS],
        "observedUndeclaredKeys": observed_undeclared_keys(),
        "workerCapabilities": worker_capabilities or {},
        "guestArtifactSHA256": dict(sorted((guest_artifact_sha256 or {}).items())),
    }


# ---------------------------------------------------------------------------
# Frame declarations
# ---------------------------------------------------------------------------

# --- exec channel ----------------------------------------------------------

EXEC_STARTED = frame(
    FrameSpec("exec_started", Routing.CHANNEL, fields=("channelID",))
)
EXEC_STDOUT = frame(
    FrameSpec("exec_stdout", Routing.CHANNEL, fields=("channelID", "bytes"))
)
EXEC_STDERR = frame(
    FrameSpec("exec_stderr", Routing.CHANNEL, fields=("channelID", "bytes"))
)
EXEC_STDIN_ACK = frame(
    FrameSpec(
        "exec_stdin_ack",
        Routing.CHANNEL,
        fields=("channelID",),
        droppable_when_unrouted=True,
    )
)
EXEC_SIGNAL_ACK = frame(
    FrameSpec(
        "exec_signal_ack",
        Routing.CHANNEL,
        fields=("channelID",),
        droppable_when_unrouted=True,
    )
)
EXEC_RESULT = frame(
    FrameSpec(
        "exec_result",
        Routing.CHANNEL_FINAL,
        fields=("channelID", "exitCode", "elapsedMs", "truncated"),
    )
)
EXEC_TIMEOUT = frame(
    FrameSpec("exec_timeout", Routing.CHANNEL_FINAL, fields=("channelID", "elapsedMs"))
)
EXEC_ERROR = frame(
    FrameSpec(
        "exec_error",
        Routing.CHANNEL_FINAL,
        fields=("channelID", "code", "message"),
        open_fields=("resource", "limit", "observed"),
        # An exec_error whose channel is already gone carries no information the
        # host can act on. Declaring the code makes the drop a declared property
        # instead of a match on the error's message prose.
        droppable_codes=(ExecErrorCode.INTERNAL_ERROR.value,),
    )
)

# --- lifecycle -------------------------------------------------------------

SPAWNED = frame(FrameSpec("spawned", Routing.CONTROL, fields=("id", "pid")))
KILLED = frame(FrameSpec("killed", Routing.CONTROL, fields=("id",)))
ABORTED = frame(FrameSpec("aborted", Routing.CONTROL, fields=("id",)))
SHUTTING_DOWN = frame(FrameSpec("shutting_down", Routing.CONTROL))

# --- configuration and status ---------------------------------------------

CONFIGURED = frame(
    FrameSpec(
        "configured",
        Routing.CONTROL,
        fields=("guestSudoMode", "cpuQuotaPercent", "maxOpenFilesPerProcess", "execUser"),
    )
)
STATUS = frame(FrameSpec("status", Routing.CONTROL, fields=("workers", "execs")))
IDLE_STATUS = frame(
    FrameSpec(
        "idle_status",
        Routing.CONTROL,
        fields=("idle", "workers", "execs"),
        optional_fields=("observedUndeclaredKeys",),
    )
)
SNAPSHOT_READY = frame(
    FrameSpec("snapshot_ready", Routing.CONTROL, fields=("workers", "execs"))
)

# --- authentication --------------------------------------------------------

# The auth frames deliberately do not carry a version. Guests before this module
# stamped `supervisorVersion` on four separate frames and no host ever read one
# of them; `described` is now the single producer site and the vocabulary
# handshake is its single consumer.
AUTH_CHALLENGE = frame(
    FrameSpec("auth_challenge", Routing.CONTROL, fields=("nonce",))
)
AUTH_OK = frame(FrameSpec("auth_ok", Routing.CONTROL))
AUTH_FAILED = frame(FrameSpec("auth_failed", Routing.CONTROL, fields=("reason",)))
AUTH_ROTATED = frame(FrameSpec("auth_rotated", Routing.CONTROL))

# --- vocabulary declaration ------------------------------------------------

DESCRIBED = frame(
    FrameSpec(
        "described",
        Routing.CONTROL,
        fields=(
            "supervisorProtocolVersion",
            "protocolVersion",
            "accepts",
            "commands",
            "frames",
            "errorCodes",
            "quotaResources",
            "openRegions",
            "observedUndeclaredKeys",
            "workerCapabilities",
            "guestArtifactSHA256",
        ),
    )
)

# --- generic control error -------------------------------------------------

ERROR = frame(
    FrameSpec(
        "error",
        Routing.CONTROL,
        fields=("message",),
        open_fields=("code", "resource", "limit", "observed"),
    )
)
