"""Real local socket mechanics with synthetic data; no watchdog is launched."""

import array
import errno
import importlib
import os
import socket
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only transport")


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    return importlib.import_module("nts_health_transport")


@pytest.fixture
def pair(module):
    left, right = module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)
    try:
        yield left, right
    finally:
        left.close()
        right.close()


def test_bidirectional_boundaries_and_configuration(pair):
    left, right = pair
    for end in pair:
        assert not end._socket.getblocking()
        assert not end._socket.get_inheritable()
        assert end._socket.getsockname() in ("", b"")
        assert 8192 <= end.buffers.send_bytes <= 16384
        assert 8192 <= end.buffers.receive_bytes <= 16384
    left.send(b"first")
    left.send(b"second")
    right.send(b"reply")
    assert right.receive() == b"first"
    assert right.receive() == b"second"
    assert left.receive() == b"reply"


def test_packet_limit(pair, module):
    left, right = pair
    packet = b"x" * module.PACKET_BYTES
    left.send(packet)
    assert right.receive() == packet
    with pytest.raises(module.TransportError, match="INVALID_PACKET"):
        left.send(packet + b"x")
    with pytest.raises(module.TransportError, match="ENDPOINT_CLOSED"):
        left.send(b"later")


@pytest.mark.parametrize("invalid", [b"", bytearray(b"x"), "text", None, True])
def test_invalid_send_poisoned(pair, module, invalid):
    with pytest.raises(module.TransportError, match="INVALID_PACKET") as error:
        pair[0].send(invalid)
    assert error.value.cleanup_complete
    assert pair[0]._socket is None


def test_empty_receive_does_not_block_or_close(pair, module):
    left, right = pair
    with pytest.raises(module.WouldBlock, match="RECEIVE_WOULD_BLOCK"):
        right.receive()
    left.send(b"after empty queue")
    assert right.receive() == b"after empty queue"


def test_bounded_queue_backpressure_and_no_loss(pair, module):
    left, right = pair
    sent = []
    for number in range(1024):
        packet = str(number).encode()
        try:
            left.send(packet)
        except module.WouldBlock:
            break
        sent.append(packet)
    else:
        pytest.fail("Queue failed to reach bounded backpressure")
    assert sent
    assert [right.receive() for _ in sent] == sent
    with pytest.raises(module.WouldBlock):
        right.receive()
    left.send(b"explicit next operation")
    assert right.receive() == b"explicit next operation"


@pytest.mark.parametrize("size", [2049, 4096])
def test_receive_truncation_is_fatal(pair, module, size):
    left, right = pair
    left._socket.send(b"x" * size)
    with pytest.raises(module.TransportError, match="PACKET_TRUNCATED"):
        right.receive()
    assert right._socket is None


def test_empty_record_rejected(pair, module):
    pair[0]._socket.send(b"")
    with pytest.raises(module.TransportError, match="EMPTY_OR_CLOSED"):
        pair[1].receive()


def test_peer_closed_receive(pair, module):
    pair[0].close()
    with pytest.raises(module.TransportError, match="EMPTY_OR_CLOSED"):
        pair[1].receive()


def test_peer_closed_send(pair, module):
    pair[0].close()
    with pytest.raises(module.TransportError, match="PEER_CLOSED"):
        pair[1].send(b"test")


def test_passed_descriptors_discarded_without_leak(pair, module):
    left, right = pair
    fd = os.open("/dev/null", os.O_RDONLY)
    try:
        before = set(os.listdir("/proc/self/fd"))
        rights = array.array("i", [fd] * 8)
        left._socket.sendmsg(
            [b"not health"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)]
        )
        with pytest.raises(module.TransportError, match="ANCILLARY_REJECTED"):
            right.receive()
        after = set(os.listdir("/proc/self/fd"))
        # The receiver endpoint closed; no received rights entered our FD table.
        assert len(after) == len(before) - 1
        os.fstat(fd)  # The original sender-owned descriptor remains intact.
    finally:
        os.close(fd)


def test_credentials_ancillary_rejected(pair, module):
    left, right = pair
    right._socket.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    left.send(b"unexpected credential data")
    with pytest.raises(module.TransportError, match="ANCILLARY_REJECTED"):
        right.receive()


def test_close_is_idempotent_and_releases_descriptors(pair, module):
    fd = pair[0]._socket.fileno()
    pair[0].close()
    pair[0].close()
    with pytest.raises(OSError):
        os.fstat(fd)
    with pytest.raises(module.TransportError, match="ENDPOINT_CLOSED"):
        pair[0].receive()


@pytest.mark.parametrize(
    "requested,ceiling",
    [
        (True, 16384),
        (8192, True),
        (0, 16384),
        (2048, 4096),
        (1048577, 2097154),
        (8192, 8192),
        (8192, 2097153),
        (8192.0, 16384),
    ],
)
def test_invalid_configuration_before_socket_creation(
    module, monkeypatch, requested, ceiling
):
    def unexpected(*args):
        pytest.fail("Invalid input reached socket creation")

    monkeypatch.setattr(module.socket, "socketpair", unexpected)
    with pytest.raises(module.TransportError, match="INVALID_CONFIGURATION"):
        module.create_pair(buffer_bytes=requested, effective_buffer_limit=ceiling)


def test_unsupported_platform(module, monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "unsupported")
    with pytest.raises(module.TransportError, match="UNSUPPORTED_PLATFORM"):
        module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)


def test_missing_socket_capability(module, monkeypatch):
    monkeypatch.delattr(module.socket, "SOCK_SEQPACKET")
    with pytest.raises(module.TransportError, match="UNSUPPORTED_PLATFORM"):
        module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)


class SocketProxy:
    """Inject one deterministic failure while retaining real owned descriptors."""

    def __init__(self, owned, method, action):
        self.owned = owned
        self.method = method
        self.action = action
        self.calls = 0

    def __getattr__(self, name):
        if name == self.method:
            return self.inject
        return getattr(self.owned, name)

    def inject(self, *args):
        self.calls += 1
        if isinstance(self.action, BaseException):
            raise self.action
        return self.action


@pytest.mark.parametrize(
    "method,action",
    [
        ("setsockopt", OSError("private setup details")),
        ("getblocking", True),
        ("get_inheritable", True),
        ("getsockname", "unexpected name"),
        ("getsockopt", 0),
    ],
)
def test_partial_setup_closes_both_ends(module, monkeypatch, method, action):
    # Preserve the required family/type while controlling setup failures.
    original = socket.socketpair
    real_pair = original(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK)
    proxy = SocketProxy(real_pair[1], method, action)
    monkeypatch.setattr(
        module.socket, "socketpair", lambda *args: (real_pair[0], proxy)
    )
    with pytest.raises(module.TransportError, match="SETUP_FAILED") as error:
        module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)
    assert error.value.cleanup_complete
    assert all(end.fileno() == -1 for end in real_pair)


def test_kernel_buffer_over_ceiling_closes_setup(module, monkeypatch):
    original = socket.socketpair
    real_pair = original(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK)

    class Oversized(SocketProxy):
        def getsockopt(self, level, option):
            if option == socket.SO_TYPE:
                return socket.SOCK_SEQPACKET
            return 16385

    proxy = Oversized(real_pair[1], "unused", None)
    monkeypatch.setattr(
        module.socket, "socketpair", lambda *args: (real_pair[0], proxy)
    )
    with pytest.raises(module.TransportError, match="SETUP_FAILED"):
        module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)
    assert all(end.fileno() == -1 for end in real_pair)


@pytest.mark.parametrize(
    "method,action,reason",
    [
        ("send", 1, "INCOMPLETE_SEND"),
        ("send", OSError(errno.EIO, "private details"), "SEND_FAILED"),
        ("recvmsg", OSError(errno.EIO, "private details"), "RECEIVE_FAILED"),
        ("recvmsg", (b"data", [], 1 << 20, None), "UNEXPECTED_FLAGS"),
    ],
)
def test_operation_failure_once_and_closed(pair, module, method, action, reason):
    left, _ = pair
    proxy = SocketProxy(left._socket, method, action)
    left._socket = proxy
    with pytest.raises(module.TransportError, match=reason) as error:
        if method == "send":
            left.send(b"data")
        else:
            left.receive()
    assert str(error.value) == reason
    assert error.value.cleanup_complete
    assert proxy.calls == 1
    assert left._socket is None


def test_close_failure_remains_explicit(pair, module):
    left, _ = pair
    owned = left._socket
    proxy = SocketProxy(owned, "close", OSError("private close details"))
    left._socket = proxy
    try:
        with pytest.raises(module.TransportError, match="CLOSE_FAILED") as error:
            left.close()
        assert not error.value.cleanup_complete
        left.close()  # Never retry an uncertain descriptor close.
        assert proxy.calls == 1
        with pytest.raises(module.TransportError) as error:
            left.receive()
        assert not error.value.cleanup_complete
    finally:
        owned.close()  # Only the fixture knows this injected close never executed.


def test_creation_error_exposes_no_private_details(module, monkeypatch):
    def denied(*args):
        raise PermissionError("private creation detail")

    monkeypatch.setattr(module.socket, "socketpair", denied)
    with pytest.raises(module.TransportError, match="SETUP_FAILED") as error:
        module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)
    assert str(error.value) == "SETUP_FAILED"
    assert error.value.cleanup_complete


def test_setup_cleanup_attempts_both_ends_even_after_close_error(module, monkeypatch):
    original = socket.socketpair
    real = original(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK)
    left = SocketProxy(real[0], "close", OSError("injected close error"))
    right = SocketProxy(real[1], "setsockopt", OSError("injected setup error"))
    monkeypatch.setattr(module.socket, "socketpair", lambda *args: (left, right))
    try:
        with pytest.raises(module.TransportError, match="SETUP_FAILED") as error:
            module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)
        assert not error.value.cleanup_complete
        assert left.calls == 1
        assert real[1].fileno() == -1
    finally:
        real[0].close()
        real[1].close()


@pytest.mark.parametrize("method", ["send", "recvmsg"])
def test_would_block_is_one_attempt_without_internal_retry(pair, module, method):
    endpoint = pair[0]
    proxy = SocketProxy(endpoint._socket, method, BlockingIOError(errno.EAGAIN, "full"))
    endpoint._socket = proxy
    with pytest.raises(module.WouldBlock):
        if method == "send":
            endpoint.send(b"synthetic")
        else:
            endpoint.receive()
    assert proxy.calls == 1
    assert endpoint._socket is proxy


def test_synthetic_protocol_exchange_over_transport(pair, module):
    protocol = importlib.import_module("nts_health_protocol")
    identity = protocol.Lifetime(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "a" * 64,
        "b" * 32,
        123,
        456,
        10000,
    )
    state = protocol.Session(
        identity,
        response_timeout_ns=100,
        max_generation_age_ns=50,
        token_factory=lambda: "c" * 64,
    )
    left, right = pair
    left.send(state.challenge(10))
    right.send(protocol.respond(right.receive(), identity, 20))
    assert state.accept(left.receive(), 30) is None
    # These supplied references and times are synthetic, not process attestation.
