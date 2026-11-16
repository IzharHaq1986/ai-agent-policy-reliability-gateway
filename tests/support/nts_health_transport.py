"""Private Linux packet transport; no watchdog identity or execution authority."""

import errno
import os  # Duplicate borrowed descriptors without taking ownership of them.
import select  # Inspect peer closure with a zero-time, nonblocking poll.
import socket
import stat  # Reject non-socket objects before wrapping an owned duplicate.
import sys
import threading  # Restrict adoption to a single-owner Python bootstrap context.
from dataclasses import dataclass
from typing import NoReturn

from nts_health_protocol import PACKET_BYTES

_MAX_BUFFER = 1024 * 1024
_PEER_ERRORS = {errno.EPIPE, errno.ECONNRESET, errno.ENOTCONN}


class TransportError(RuntimeError):
    """Report a fixed reason and whether owned-descriptor cleanup was confirmed."""

    def __init__(self, reason: str, *, cleanup_complete: bool) -> None:
        super().__init__(reason)
        self.cleanup_complete = cleanup_complete


class WouldBlock(RuntimeError):
    """No progress: the caller must apply its existing deadline, never busy-retry."""


@dataclass(frozen=True)
class BufferObservation:
    """Readback is configuration evidence, not a queued-message count."""

    send_bytes: int
    receive_bytes: int


def _close(sockets: tuple[socket.socket, ...]) -> bool:
    complete = True
    for owned in sockets:
        try:
            owned.close()
        except OSError:
            # Never retry close: the descriptor number may already be reused.
            complete = False
    return complete


class Endpoint:
    """Own one created or checked-adopted endpoint; no launch or FD handoff API.

    Use from one trusted owner only. The helper has no application queue and
    neither parses health messages nor polls for readiness.
    """

    def __init__(self, owned: socket.socket, buffers: BufferObservation) -> None:
        self._socket: socket.socket | None = owned
        self.buffers = buffers
        self._cleanup_complete = True

    def close(self) -> None:
        """Release this endpoint once, including after earlier transport failure."""
        owned, self._socket = self._socket, None
        if owned is not None and not _close((owned,)):
            self._cleanup_complete = False
            raise TransportError("CLOSE_FAILED", cleanup_complete=False)

    def _fail(self, reason: str) -> NoReturn:
        owned, self._socket = self._socket, None
        complete = (owned is None or _close((owned,))) and self._cleanup_complete
        self._cleanup_complete = complete
        raise TransportError(reason, cleanup_complete=complete) from None

    def _owned(self) -> socket.socket:
        if self._socket is None:
            raise TransportError(
                "ENDPOINT_CLOSED", cleanup_complete=self._cleanup_complete
            )
        return self._socket

    def send(self, packet: bytes) -> None:
        """Attempt one whole packet; never send a remainder or retry internally."""
        owned = self._owned()
        if type(packet) is not bytes or not 0 < len(packet) <= PACKET_BYTES:
            self._fail("INVALID_PACKET")
        try:
            count = owned.send(packet, socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL)
        except BlockingIOError:
            raise WouldBlock("SEND_WOULD_BLOCK") from None
        except OSError as error:
            self._fail("PEER_CLOSED" if error.errno in _PEER_ERRORS else "SEND_FAILED")
        if count != len(packet):
            self._fail("INCOMPLETE_SEND")

    def receive(self) -> bytes:
        """Read at most one packet; malformed input permanently closes this end."""
        owned = self._owned()
        try:
            packet, ancillary, flags, _ = owned.recvmsg(
                PACKET_BYTES, 0, socket.MSG_DONTWAIT | socket.MSG_CMSG_CLOEXEC
            )
        except BlockingIOError:
            raise WouldBlock("RECEIVE_WOULD_BLOCK") from None
        except OSError as error:
            self._fail(
                "PEER_CLOSED" if error.errno in _PEER_ERRORS else "RECEIVE_FAILED"
            )
        # Linux discards excess SCM_RIGHTS and closes them when ancbufsize is zero.
        # MSG_CTRUNC makes that input a failure rather than a valid health reply.
        if ancillary or flags & socket.MSG_CTRUNC:
            self._fail("ANCILLARY_REJECTED")
        if flags & socket.MSG_TRUNC:
            self._fail("PACKET_TRUNCATED")
        if flags & ~(socket.MSG_CMSG_CLOEXEC | socket.MSG_EOR):
            self._fail("UNEXPECTED_FLAGS")
        if not packet:
            # A zero-length record and orderly EOF are indistinguishable here.
            # Neither is valid health evidence; do not invent a precise diagnosis.
            self._fail("EMPTY_OR_CLOSED")
        return packet


def _configuration(buffer_bytes: int, effective_buffer_limit: int) -> None:
    """Keep creation and adoption under the same explicit resource bounds."""
    if not (
        type(buffer_bytes) is int
        and 2 * PACKET_BYTES <= buffer_bytes <= _MAX_BUFFER
        and type(effective_buffer_limit) is int
        and 2 * buffer_bytes <= effective_buffer_limit <= 2 * _MAX_BUFFER
    ):
        raise TransportError("INVALID_CONFIGURATION", cleanup_complete=True)


def _platform() -> None:
    """Reject unsupported transport semantics before acquiring owned descriptors."""
    constants = (
        "AF_UNIX",  # Require local IPC instead of a network transport.
        "SOCK_SEQPACKET",  # Preserve complete ordered message boundaries.
        "SOCK_NONBLOCK",  # Create without an implicit readiness wait.
        "SOCK_CLOEXEC",  # Prevent unintended endpoint inheritance across exec.
        "MSG_DONTWAIT",  # Keep each data operation nonblocking.
        "MSG_NOSIGNAL",  # Turn peer closure into an error instead of SIGPIPE.
        "MSG_CMSG_CLOEXEC",  # Protect any kernel-level received-FD handling.
        "MSG_CTRUNC",  # Detect discarded unexpected ancillary input.
        "MSG_TRUNC",  # Detect incomplete packet evidence.
        "MSG_EOR",  # Recognize a supported end-of-record result flag.
    )
    # No fallback may substitute different descriptor or message semantics.
    if sys.platform != "linux" or not all(hasattr(socket, name) for name in constants):
        raise TransportError("UNSUPPORTED_PLATFORM", cleanup_complete=True)


def _read_buffers(
    endpoint: socket.socket, buffer_bytes: int, effective_buffer_limit: int
) -> BufferObservation:
    """Read existing limits without changing shared socket configuration."""
    # Linux readback can be kernel-adjusted; these reads do not resize queues.
    send = endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
    receive = endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    # Both readbacks must fit the same accepted interval used during creation.
    if not all(
        buffer_bytes <= size <= effective_buffer_limit for size in (send, receive)
    ):
        raise ValueError(
            "buffer bounds"
        )  # Callers convert this fixed rejection appropriately.
    return BufferObservation(send, receive)  # This is not an exact message-count bound.


def create_pair(
    *, buffer_bytes: int, effective_buffer_limit: int
) -> tuple[Endpoint, Endpoint]:
    """Create two owned, unnamed, nonblocking, non-inheritable Linux endpoints.

    Buffer requests and accepted kernel-adjusted ceilings are explicit. Both
    send queues are bounded; Unix SO_RCVBUF readback is not receive-flow control.
    """
    _configuration(
        buffer_bytes, effective_buffer_limit
    )  # Reuse explicit resource bounds.
    _platform()  # Preserve the existing Linux-only semantics.
    owned: tuple[socket.socket, ...] = ()
    try:
        owned = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC,
        )
        observations = []
        for endpoint in owned:
            # Atomic creation flags precede any potential descriptor handoff.
            if (
                endpoint.getblocking()
                or endpoint.get_inheritable()
                or endpoint.family != socket.AF_UNIX
                or endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
                != socket.SOCK_SEQPACKET
                or endpoint.getsockname() not in ("", b"")
            ):
                raise ValueError
            for option in (socket.SO_SNDBUF, socket.SO_RCVBUF):
                endpoint.setsockopt(socket.SOL_SOCKET, option, buffer_bytes)
            # Share readback validation without changing existing creation behavior.
            observations.append(
                _read_buffers(endpoint, buffer_bytes, effective_buffer_limit)
            )
        return Endpoint(owned[0], observations[0]), Endpoint(owned[1], observations[1])
    except (OSError, ValueError, AttributeError):
        complete = _close(owned)
        raise TransportError("SETUP_FAILED", cleanup_complete=complete) from None


def adopt_inherited(
    borrowed_fd: int,
    *,
    expected_device: int,
    expected_inode: int,
    buffer_bytes: int,
    effective_buffer_limit: int,
) -> Endpoint:
    """Validate an owned duplicate of a trusted bootstrap's borrowed endpoint.

    Expected identity must come from trusted launch code, never message claims.
    Success leaves the borrowed original open. Its owner must close that spare
    copy before further handoff or readiness; this helper never closes it.
    """
    _configuration(buffer_bytes, effective_buffer_limit)  # Reject invalid bounds first.
    _platform()  # Do not fall back to weaker platform or socket semantics.
    # Standard streams and malformed identity values cannot be bootstrap inputs.
    if not (
        type(borrowed_fd) is int
        and 2 < borrowed_fd <= (1 << 31) - 1
        and type(expected_device) is int
        and 0 <= expected_device < (1 << 64)
        and type(expected_inode) is int
        and 0 < expected_inode < (1 << 64)
    ):
        raise TransportError("INVALID_INHERITED_REFERENCE", cleanup_complete=True)
    if (
        threading.active_count() != 1
    ):  # Concurrent close/reuse is outside this operation.
        raise TransportError("UNSAFE_BOOTSTRAP_CONTEXT", cleanup_complete=True)
    raw_owned: int | None = (
        None  # Track ownership before a Python socket object exists.
    )
    managed: socket.socket | None = None  # Never wrap the caller's original descriptor.
    transferred = False  # Only a fully validated result may retain our owned duplicate.
    try:  # Any failure, including interrupted validation, releases acquired copies.
        raw_owned = os.dup(
            borrowed_fd
        )  # A duplicate gives us independent close ownership.
        os.set_inheritable(
            raw_owned, False
        )  # Change only the duplicate's per-FD exec flag.
        if os.get_inheritable(
            raw_owned
        ):  # Verify the flag instead of assuming success.
            raise ValueError(
                "inheritance flag"
            )  # Never return an inheritable owned copy.
        observed = os.fstat(
            raw_owned
        )  # Compare kernel identity, not descriptor-number equality.
        if (
            not stat.S_ISSOCK(observed.st_mode)
            or observed.st_dev != expected_device
            or observed.st_ino != expected_inode
        ):
            raise ValueError(
                "identity mismatch"
            )  # A wrong source is rejected, never closed.
        if os.get_blocking(
            raw_owned
        ):  # Read the actual shared kernel mode before wrapping.
            raise ValueError("blocking source")  # Never fix a blocking borrowed source.
        managed = socket.socket(
            fileno=raw_owned
        )  # Adopt only the duplicate that we own.
        raw_owned = None  # The socket object is now the sole owner of that duplicate.
        if (
            managed.family != socket.AF_UNIX
            or managed.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
            != socket.SOCK_SEQPACKET
            or managed.getsockname() not in ("", b"")
            or managed.getpeername() not in ("", b"")
        ):
            raise ValueError(
                "socket profile"
            )  # Require a connected unnamed local packet socket.
        buffers = _read_buffers(
            managed, buffer_bytes, effective_buffer_limit
        )  # No resizing.
        poller = (
            select.poll()
        )  # Inspect current failure flags without waiting for readiness.
        failure_flags = (
            select.POLLHUP | select.POLLERR | select.POLLNVAL | select.POLLRDHUP
        )
        poller.register(
            managed, failure_flags
        )  # Do not consume or peek at queued messages.
        if poller.poll(
            0
        ):  # Zero timeout makes this an instantaneous closure/error observation.
            raise ValueError(
                "closed peer"
            )  # A historical peer address is not current viability.
        # Normalize wrapper timeout state only after the kernel mode passed.
        managed.setblocking(False)  # The shared kernel mode is already nonblocking.
        if os.get_blocking(managed.fileno()) or managed.get_inheritable():
            raise ValueError(
                "owned descriptor flags"
            )  # Verify the result before transfer.
        result = Endpoint(
            managed, buffers
        )  # Reuse existing bounded send/receive behavior.
        transferred = True  # The returned endpoint now owns this validated duplicate.
        return result  # This validates an endpoint, not a process or message sender.
    except (
        ValueError,
        AttributeError,
    ):  # Bad observations never become an accepted endpoint.
        raise TransportError("ADOPTION_REJECTED", cleanup_complete=True) from None
    except OSError:  # Missing, denied, or invalidated kernel evidence blocks adoption.
        raise TransportError("ADOPTION_UNAVAILABLE", cleanup_complete=True) from None
    finally:  # Never close the borrowed descriptor, even after failed validation.
        if not transferred:  # Only acquired owned copies belong to failure cleanup.
            complete = (
                True  # Preserve uncertain close outcomes instead of retrying them.
            )
            if (
                managed is not None
            ):  # The socket object owns the duplicate after wrapping.
                complete = _close(
                    (managed,)
                )  # Reuse existing one-attempt socket cleanup.
            elif (
                raw_owned is not None
            ):  # Wrapping failed or identity validation rejected early.
                try:  # Release only the duplicate created by this operation.
                    os.close(raw_owned)  # Never close the supplied borrowed FD number.
                except (
                    OSError
                ):  # The close may have occurred despite an error response.
                    complete = False  # Do not retry an uncertain descriptor number.
            if (
                not complete
            ):  # Cleanup uncertainty overrides any earlier rejection reason.
                raise TransportError("ADOPTION_CLEANUP_FAILED", cleanup_complete=False)
