"""Private Linux packet transport; no watchdog identity or execution authority."""

import errno
import socket
import sys
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
    """Own one endpoint returned by create_pair; no adoption or FD handoff API.

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


def create_pair(
    *, buffer_bytes: int, effective_buffer_limit: int
) -> tuple[Endpoint, Endpoint]:
    """Create two owned, unnamed, nonblocking, non-inheritable Linux endpoints.

    Buffer requests and accepted kernel-adjusted ceilings are explicit. Both
    send queues are bounded; Unix SO_RCVBUF readback is not receive-flow control.
    """
    if not (
        type(buffer_bytes) is int
        and 2 * PACKET_BYTES <= buffer_bytes <= _MAX_BUFFER
        and type(effective_buffer_limit) is int
        and 2 * buffer_bytes <= effective_buffer_limit <= 2 * _MAX_BUFFER
    ):
        raise TransportError("INVALID_CONFIGURATION", cleanup_complete=True)
    constants = (
        "AF_UNIX",
        "SOCK_SEQPACKET",
        "SOCK_NONBLOCK",
        "SOCK_CLOEXEC",
        "MSG_DONTWAIT",
        "MSG_NOSIGNAL",
        "MSG_CMSG_CLOEXEC",
        "MSG_CTRUNC",
        "MSG_TRUNC",
        "MSG_EOR",
    )
    if sys.platform != "linux" or not all(hasattr(socket, name) for name in constants):
        raise TransportError("UNSUPPORTED_PLATFORM", cleanup_complete=True)
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
            send = endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
            receive = endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            if not all(
                buffer_bytes <= size <= effective_buffer_limit
                for size in (send, receive)
            ):
                raise ValueError
            observations.append(BufferObservation(send, receive))
        return Endpoint(owned[0], observations[0]), Endpoint(owned[1], observations[1])
    except (OSError, ValueError, AttributeError):
        complete = _close(owned)
        raise TransportError("SETUP_FAILED", cleanup_complete=complete) from None
