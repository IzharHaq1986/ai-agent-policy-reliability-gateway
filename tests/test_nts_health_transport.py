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


def adopted(module, source, **changes):
    """Supply kernel identity from this trusted test, not from a received packet."""
    fd = source.fileno()  # Treat the existing socket descriptor as borrowed.
    observed = os.fstat(fd)  # Capture the expected identity independently.
    arguments = {  # Keep every bootstrap input explicit in the test.
        "borrowed_fd": fd,
        "expected_device": observed.st_dev,
        "expected_inode": observed.st_ino,
        "buffer_bytes": 8192,
        "effective_buffer_limit": 16384,
    }
    arguments.update(
        changes
    )  # Negative tests alter exactly the condition being checked.
    return module.adopt_inherited(**arguments)  # Adoption must never close this source.


def source_state(source):
    """Read the borrowed source's flags and buffers to detect unintended changes."""
    return (  # Duplicates share socket state, but have separate exec-inheritance flags.
        os.get_blocking(source.fileno()),
        os.get_inheritable(source.fileno()),
        source.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF),
        source.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF),
    )


def test_adopted_copy_survives_source_close(pair, module):
    left, right = pair  # Use the real bounded private transport pair.
    source = left._socket  # Model a trusted bootstrap's inherited descriptor.
    source.set_inheritable(True)  # Simulate pass_fds leaving the source inheritable.
    before = source_state(source)  # Retain an independent borrowed-state witness.
    copy = adopted(module, source)  # Validate and own a new duplicate only.
    try:  # The test must close its adopted copy on every path.
        assert (
            source_state(source) == before
        )  # Adoption must not change the borrowed state.
        assert (
            copy._socket.fileno() != source.fileno()
        )  # Close ownership is independent.
        assert (
            not copy._socket.get_inheritable()
        )  # The owned copy cannot leak across exec.
        assert (
            not copy._socket.getblocking()
        )  # Wrapper bookkeeping matches actual kernel mode.
        left.close()  # The trusted caller explicitly releases its original copy.
        right.send(b"still connected")  # The owned duplicate keeps the endpoint usable.
        assert (
            copy.receive() == b"still connected"
        )  # Source closure must not close the duplicate.
        copy.send(
            b"reply"
        )  # Existing bounded transport behavior works in both directions.
        assert (
            right.receive() == b"reply"
        )  # No new message protocol or authority was introduced.
    finally:  # Release only the independently owned duplicate.
        copy.close()  # This does not affect any unrelated descriptor.


def test_closing_adopted_copy_preserves_borrowed_source(pair, module):
    left, right = pair  # Both original endpoints remain owned by this fixture.
    copy = adopted(module, left._socket)  # Acquire one extra close-owned reference.
    copy.close()  # Releasing our copy must not close the caller's original.
    right.send(
        b"original intact"
    )  # Exercise the borrowed source after adopted-copy closure.
    assert (
        left.receive() == b"original intact"
    )  # Verify actual usability, not only FD existence.


@pytest.mark.parametrize(
    "field,value",
    [
        ("borrowed_fd", True),  # Booleans cannot select an inherited endpoint.
        ("borrowed_fd", 2),  # Do not adopt standard streams.
        ("borrowed_fd", -1),  # Negative descriptors are not valid references.
        ("borrowed_fd", 1 << 31),  # Reject values outside the kernel FD argument range.
        (
            "expected_device",
            None,
        ),  # Missing trusted identity must fail before duplication.
        ("expected_device", -1),  # Device identity cannot be negative.
        ("expected_inode", True),  # Exact integer identity is required.
        ("expected_inode", 0),  # An absent inode cannot identify this socket.
        ("expected_inode", 1 << 64),  # Oversized identity values are unsupported.
    ],
)
def test_invalid_adoption_reference_never_duplicates(
    pair, module, monkeypatch, field, value
):
    source = pair[0]._socket  # Keep the valid borrowed source independently owned.

    def unexpected(_):  # Invalid references must be rejected before any FD acquisition.
        pytest.fail("Invalid reference reached duplication")

    with (
        monkeypatch.context() as fault
    ):  # Restore global OS functions before fixture cleanup.
        fault.setattr(
            module.os, "dup", unexpected
        )  # Detect unintended ownership acquisition.
        with pytest.raises(module.TransportError, match="INVALID_INHERITED_REFERENCE"):
            adopted(module, source, **{field: value})  # Alter only the invalid field.
    os.fstat(source.fileno())  # The borrowed descriptor remains open and unchanged.


@pytest.mark.parametrize(
    "field,value",
    [
        ("buffer_bytes", 0),  # Zero-sized resource configuration is unsupported.
        (
            "effective_buffer_limit",
            8192,
        ),  # The ceiling must account for kernel adjustment.
    ],
)
def test_adoption_reuses_configuration_gate(pair, module, field, value):
    with pytest.raises(module.TransportError, match="INVALID_CONFIGURATION"):
        adopted(
            module, pair[0]._socket, **{field: value}
        )  # Keep existing buffer bounds intact.
    pair[1].send(b"unchanged")  # Rejection must not close the source.
    assert pair[0].receive() == b"unchanged"  # Independently verify preservation.


@pytest.mark.parametrize("field", ["expected_device", "expected_inode"])
def test_identity_mismatch_preserves_borrowed_socket(pair, module, field):
    left, right = pair  # Retain both original endpoints for a usability check.
    source = left._socket  # Supply a borrowed descriptor, never a transferred one.
    source.set_inheritable(True)  # Even this source flag must remain untouched.
    observed = os.fstat(source.fileno())  # Record the actual trusted identity.
    value = (
        (observed.st_dev ^ 1)
        if field == "expected_device"
        else ((observed.st_ino ^ 1) or 2)
    )
    before = source_state(source)  # Snapshot shared configuration before rejection.
    count = len(os.listdir("/proc/self/fd"))  # Detect leaked duplicates independently.
    with pytest.raises(module.TransportError, match="ADOPTION_REJECTED") as error:
        adopted(
            module, source, **{field: value}
        )  # Supply the wrong identity for this source.
    assert error.value.cleanup_complete  # The rejected duplicate must be released.
    assert len(os.listdir("/proc/self/fd")) == count  # No extra FD survives rejection.
    assert (
        source_state(source) == before
    )  # No borrowed socket setting was repaired or reset.
    right.send(b"source usable")  # A rejected copy must not damage the original.
    assert (
        left.receive() == b"source usable"
    )  # Confirm actual transport operation afterward.


def test_closed_descriptor_is_unavailable(module):
    fd = os.open(
        "/dev/null", os.O_RDONLY
    )  # Allocate a real descriptor for a closed-reference test.
    os.close(fd)  # Keep no owner and do not allocate another FD before adoption.
    with pytest.raises(module.TransportError, match="ADOPTION_UNAVAILABLE"):
        module.adopt_inherited(
            fd,
            expected_device=0,
            expected_inode=1,
            buffer_bytes=8192,
            effective_buffer_limit=16384,
        )


def test_non_socket_is_rejected_without_closing_it(module):
    fd = os.open(
        "/dev/null", os.O_RDONLY
    )  # A non-socket must never become a health endpoint.
    try:  # The test retains ownership of the borrowed descriptor.
        observed = os.fstat(
            fd
        )  # Supply its real identity so type validation is exercised.
        with pytest.raises(module.TransportError, match="ADOPTION_REJECTED"):
            module.adopt_inherited(
                fd,
                expected_device=observed.st_dev,
                expected_inode=observed.st_ino,
                buffer_bytes=8192,
                effective_buffer_limit=16384,
            )
        os.fstat(fd)  # Rejection must leave the borrowed original open.
    finally:  # Only the original owner releases this file.
        os.close(fd)  # No helper may close it during validation.


@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
def test_wrong_socket_type_is_not_repaired(module, kind):
    left, right = socket.socketpair(socket.AF_UNIX, kind | socket.SOCK_NONBLOCK)
    try:  # These real sockets are local fixtures, not a process handoff.
        for option in (
            socket.SO_SNDBUF,
            socket.SO_RCVBUF,
        ):  # Isolate the wrong-type condition.
            left.setsockopt(
                socket.SOL_SOCKET, option, 8192
            )  # Satisfy all buffer checks first.
        before = source_state(left)  # Wrong-type rejection must preserve shared state.
        with pytest.raises(module.TransportError, match="ADOPTION_REJECTED"):
            adopted(
                module, left
            )  # Expected identity alone cannot make the type acceptable.
        assert (
            source_state(left) == before
        )  # Do not silently substitute a packet socket.
        left.send(b"x")  # The original wrong-type socket must remain usable.
        assert right.recv(1) == b"x"  # Confirm preservation independently.
    finally:  # Release both fixture-owned endpoints.
        left.close()  # No helper owns this borrowed original.
        right.close()  # No fixture resource remains open.


def test_wrong_family_is_rejected(module):
    source = socket.socket(socket.AF_INET, socket.SOCK_STREAM | socket.SOCK_NONBLOCK)
    try:  # Do not connect, listen, or perform any external network operation.
        with pytest.raises(module.TransportError, match="ADOPTION_REJECTED"):
            adopted(
                module, source
            )  # A real Internet-family descriptor is not a local channel.
        os.fstat(source.fileno())  # Rejection leaves the borrowed original open.
    finally:  # Release only the unconnected test socket.
        source.close()  # No port or network session was established.


def test_unconnected_endpoint_is_rejected(module):
    source = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK)
    try:  # An unnamed socket alone is not an established pair.
        with pytest.raises(module.TransportError):
            adopted(module, source)  # Peer lookup must reject the disconnected source.
        os.fstat(source.fileno())  # Do not close the borrowed descriptor on failure.
    finally:  # Release the fixture-owned unconnected socket.
        source.close()  # Nothing else may assume ownership of it.


@pytest.mark.parametrize("side", [0, 1])
def test_named_endpoint_is_rejected(pair, module, side):
    source = pair[
        side
    ]._socket  # Both endpoints already satisfy the other transport bounds.
    # Naming one connected end tests local-name and peer-name rejection separately.
    address = b"\0nts-adoption-" + os.urandom(16).hex().encode("ascii")
    pair[0]._socket.bind(
        address
    )  # The private abstract name disappears on fixture cleanup.
    with pytest.raises(module.TransportError, match="ADOPTION_REJECTED"):
        adopted(
            module, source
        )  # Neither named side may become an accepted private endpoint.
    assert (
        pair[0]._socket.getsockname() == address
    )  # Adoption did not mutate or close it.
    pair[1].send(
        b"named source intact"
    )  # Rejection must preserve the original connection.
    assert (
        pair[0].receive() == b"named source intact"
    )  # Confirm actual usability afterward.


def test_blocking_source_is_rejected_unchanged(pair, module):
    source = pair[
        0
    ]._socket  # Deliberately invalidate the existing source's kernel mode.
    source.setblocking(True)  # Adoption must reject rather than repair this setting.
    before = source_state(source)  # Record the deliberately blocking profile.
    with pytest.raises(module.TransportError, match="ADOPTION_REJECTED"):
        adopted(module, source)  # Inspect actual kernel state, not wrapper assumptions.
    assert (
        source_state(source) == before
    )  # The source must remain in its original state.


@pytest.mark.parametrize("option", [socket.SO_SNDBUF, socket.SO_RCVBUF])
def test_out_of_bounds_buffers_are_not_resized(pair, module, option):
    source = pair[0]._socket  # Keep ownership in the caller throughout rejection.
    source.setsockopt(
        socket.SOL_SOCKET, option, 32768
    )  # Exceed the explicit accepted ceiling.
    before = source_state(source)  # Capture the actual kernel-adjusted value.
    with pytest.raises(module.TransportError, match="ADOPTION_REJECTED"):
        adopted(
            module, source
        )  # An inherited endpoint must already satisfy the bounds.
    assert source_state(source) == before  # No silent buffer repair is permitted.


def test_closed_peer_is_rejected_without_consuming_data(pair, module):
    left, right = pair  # An old peer address may remain available after closure.
    right.send(b"queued before close")  # Rejection must not consume diagnostic bytes.
    right.close()  # A historical connection is no longer sufficient for adoption.
    with pytest.raises(module.TransportError, match="ADOPTION_REJECTED"):
        adopted(
            module, left._socket
        )  # Zero-time peer-state observation must catch closure.
    assert (
        left.receive() == b"queued before close"
    )  # Preserve the borrowed receive queue.


def test_concurrent_bootstrap_is_rejected(pair, module, monkeypatch):
    with (
        monkeypatch.context() as fault
    ):  # Restore the global thread observer afterward.
        fault.setattr(
            module.threading, "active_count", lambda: 2
        )  # Simulate another Python owner.
        with pytest.raises(module.TransportError, match="UNSAFE_BOOTSTRAP_CONTEXT"):
            adopted(
                module, pair[0]._socket
            )  # Do not race a possible close/reuse actor.
    pair[1].send(b"still open")  # No duplicate or source closure should have occurred.
    assert pair[0].receive() == b"still open"  # Verify the original remains usable.


def test_duplicate_failure_keeps_source_open(pair, module, monkeypatch):
    def denied(_):  # Simulate failing before any owned descriptor is returned.
        raise PermissionError("private duplication detail")  # Do not echo this payload.

    with (
        monkeypatch.context() as fault
    ):  # Restore OS operations before fixture cleanup.
        fault.setattr(
            module.os, "dup", denied
        )  # Exercise the actual duplication boundary.
        with pytest.raises(
            module.TransportError, match="ADOPTION_UNAVAILABLE"
        ) as error:
            adopted(
                module, pair[0]._socket
            )  # A failed duplicate cannot become an endpoint.
        assert error.value.cleanup_complete  # There was no acquired copy to release.
    pair[1].send(b"source preserved")  # The helper never owned the original.
    assert pair[0].receive() == b"source preserved"  # Rejection did not close it.


def test_socket_wrapper_failure_closes_only_duplicate(pair, module, monkeypatch):
    count = len(
        os.listdir("/proc/self/fd")
    )  # Detect ownership leaks after raw duplication.

    def denied(
        *args, **kwargs
    ):  # Fail before the Python socket wrapper acquires the copy.
        raise OSError(
            "private wrapper detail"
        )  # Public failure text must remain fixed.

    with (
        monkeypatch.context() as fault
    ):  # Keep teardown's normal socket behavior intact.
        fault.setattr(
            module.socket, "socket", denied
        )  # Exercise partial setup cleanup.
        with pytest.raises(module.TransportError, match="ADOPTION_UNAVAILABLE"):
            adopted(
                module, pair[0]._socket
            )  # Only the new raw duplicate can be closed.
    assert len(os.listdir("/proc/self/fd")) == count  # No raw duplicate may leak.
    pair[1].send(b"original works")  # The source must survive wrapper failure.
    assert pair[0].receive() == b"original works"  # Verify actual packet delivery.


def test_uncertain_raw_close_is_not_retried(pair, module, monkeypatch):
    original = module.os.close  # Keep the real closer for a no-leak fault injection.
    calls = []  # Record which exact owned descriptor the helper attempted to close.

    def lost_ack(fd):  # Model close success followed by lost confirmation.
        calls.append(fd)  # A retry could act on a reused numeric descriptor.
        original(fd)  # Close the actual owned duplicate so this test cannot leak it.
        raise OSError(
            "synthetic close confirmation loss"
        )  # Preserve uncertainty explicitly.

    borrowed = pair[
        0
    ]._socket.fileno()  # This descriptor must never be closed by the helper.
    with monkeypatch.context() as fault:  # Restore the closer before fixture teardown.
        fault.setattr(
            module.os, "close", lost_ack
        )  # Fail only the helper's raw-duplicate cleanup.
        with pytest.raises(
            module.TransportError, match="ADOPTION_CLEANUP_FAILED"
        ) as error:
            adopted(
                module,
                pair[0]._socket,
                expected_device=os.fstat(borrowed).st_dev ^ 1,
            )  # Reject before socket wrapping.
        assert (
            not error.value.cleanup_complete
        )  # A real close is not a confirmed cleanup receipt.
    assert (
        len(calls) == 1 and calls[0] != borrowed
    )  # No borrowed closure and no blind retry.
    pair[1].send(b"borrowed intact")  # The original remains owned by its caller.
    assert pair[0].receive() == b"borrowed intact"  # Independently check preservation.


def test_uncertain_wrapped_close_is_explicit(pair, module, monkeypatch):
    original = module._close  # Retain the real one-attempt socket cleanup.

    def lost_ack(
        sockets,
    ):  # Preserve the actual resource effect while losing confidence.
        assert original(
            sockets
        )  # Clean the real duplicate before simulating a failed receipt.
        return False  # The adoption operation must not hide this uncertainty.

    with monkeypatch.context() as fault:  # Restore normal pair teardown cleanup.
        fault.setattr(
            module, "_close", lost_ack
        )  # Affect only the owned duplicate's release.
        pair[0]._socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
        with pytest.raises(
            module.TransportError, match="ADOPTION_CLEANUP_FAILED"
        ) as error:
            adopted(
                module, pair[0]._socket
            )  # Reject after wrapping, at buffer validation.
        assert (
            not error.value.cleanup_complete
        )  # No success or pristine cleanup claim is allowed.
    os.fstat(pair[0]._socket.fileno())  # The borrowed original must still be open.


@pytest.mark.parametrize("code", [errno.EACCES, errno.ENOTCONN])
def test_unavailable_peer_evidence_cannot_pass(pair, module, monkeypatch, code):
    original = (
        module.socket.socket
    )  # Keep real socket wrapping beneath this query fault.

    def wrap(
        *args, **kwargs
    ):  # The test still owns and validates a real duplicated descriptor.
        owned = original(
            *args, **kwargs
        )  # Do not substitute fake descriptor ownership.
        return SocketProxy(owned, "getpeername", OSError(code, "private peer detail"))

    with (
        monkeypatch.context() as fault
    ):  # Restore normal socket behavior before teardown.
        fault.setattr(
            module.socket, "socket", wrap
        )  # Model an unavailable kernel peer lookup.
        with pytest.raises(module.TransportError, match="ADOPTION_UNAVAILABLE"):
            adopted(
                module, pair[0]._socket
            )  # Missing peer evidence cannot become acceptance.
    pair[1].send(
        b"still connected"
    )  # This injected-error test preserves the real source.
    assert (
        pair[0].receive() == b"still connected"
    )  # Native peer-closure tests remain separate.


def test_inheritance_setup_failure_preserves_source(pair, module, monkeypatch):
    source = pair[0]._socket  # The caller retains its original descriptor.
    before = source_state(source)  # Detect any change to borrowed flags or buffers.
    count = len(
        os.listdir("/proc/self/fd")
    )  # Detect owned-copy leaks on partial setup.

    def denied(*args):  # Simulate failure when setting the duplicate's exec flag.
        raise PermissionError("private flag detail")  # No raw error content may escape.

    with (
        monkeypatch.context() as fault
    ):  # Restore the OS setter before fixture teardown.
        fault.setattr(
            module.os, "set_inheritable", denied
        )  # Only the owned copy is targeted.
        with pytest.raises(module.TransportError, match="ADOPTION_UNAVAILABLE"):
            adopted(
                module, source
            )  # Reject instead of returning an inheritable endpoint.
    assert (
        len(os.listdir("/proc/self/fd")) == count
    )  # The acquired duplicate must be closed.
    assert source_state(source) == before  # The borrowed source must remain unchanged.


def test_wrapped_flag_failure_rejects_owned_copy(pair, module, monkeypatch):
    original = (
        module.socket.socket
    )  # Preserve actual socket ownership and kernel identity.

    def wrap(
        *args, **kwargs
    ):  # Fault only the final returned-wrapper flag observation.
        return SocketProxy(original(*args, **kwargs), "get_inheritable", True)

    with (
        monkeypatch.context() as fault
    ):  # Prevent the injected observer from affecting teardown.
        fault.setattr(
            module.socket, "socket", wrap
        )  # A failed final flag check must reject.
        with pytest.raises(module.TransportError, match="ADOPTION_REJECTED"):
            adopted(
                module, pair[0]._socket
            )  # Never transfer a copy with unverifiable flags.
    pair[1].send(b"borrowed unaffected")  # Rejection must close only the new copy.
    assert pair[0].receive() == b"borrowed unaffected"  # Verify source preservation.


def test_unexpected_interruption_releases_duplicate(pair, module, monkeypatch):
    count = len(
        os.listdir("/proc/self/fd")
    )  # Measure actual descriptor ownership before setup.

    def interrupted(
        *args,
    ):  # Interrupt validation after the socket wrapper owns the duplicate.
        raise KeyboardInterrupt  # Cleanup must also run for interrupted validation.

    with (
        monkeypatch.context() as fault
    ):  # Restore shared validation before fixture cleanup.
        fault.setattr(
            module, "_read_buffers", interrupted
        )  # Do not alter source properties.
        with pytest.raises(KeyboardInterrupt):
            adopted(
                module, pair[0]._socket
            )  # Propagate interruption without retaining a copy.
    assert len(os.listdir("/proc/self/fd")) == count  # No owned duplicate may leak.
    pair[1].send(
        b"source survives interruption"
    )  # The borrowed descriptor was never ours to close.
    assert (
        pair[0].receive() == b"source survives interruption"
    )  # Confirm its continued usability.


def test_default_socket_timeout_does_not_change_borrowed_mode(pair, module):
    source = pair[
        0
    ]._socket  # Kernel mode is the authority for this inherited descriptor.
    before = source_state(
        source
    )  # Preserve the caller's actual flags and buffer settings.
    previous = (
        socket.getdefaulttimeout()
    )  # Restore the process-global setting on every path.
    copy = None  # Track only the new endpoint if adoption succeeds.
    try:  # Python wrapper defaults must not repair or change a borrowed socket.
        socket.setdefaulttimeout(1)  # Simulate unrelated wrapper timeout bookkeeping.
        copy = adopted(
            module, source
        )  # The owned wrapper must normalize to nonblocking.
        assert (
            copy._socket.gettimeout() == 0
        )  # No Python-level readiness wait is introduced.
        assert (
            source_state(source) == before
        )  # Shared kernel mode remains exactly unchanged.
    finally:  # Keep this global-state test isolated from all other tests.
        socket.setdefaulttimeout(
            previous
        )  # Restore the original process socket default.
        if copy is not None:  # Close only a copy that was actually acquired.
            copy.close()  # The borrowed source remains owned by the pair fixture.
