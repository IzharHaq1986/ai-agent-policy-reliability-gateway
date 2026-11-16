"""Synthetic protocol checks do not establish process or time-source trust."""

import importlib
import json
from dataclasses import replace
from pathlib import Path

import pytest


@pytest.fixture
def protocol(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    return importlib.import_module("nts_health_protocol")


@pytest.fixture
def lifetime(protocol):
    return protocol.Lifetime(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "a" * 64,
        "b" * 32,
        123,
        456,
        10000,
    )


def session(protocol, lifetime):
    tokens = iter(["c" * 64, "d" * 64, "e" * 64])
    return protocol.Session(
        lifetime,
        response_timeout_ns=100,
        max_generation_age_ns=50,
        token_factory=lambda: next(tokens),
    )


def mutated(packet, **changes):
    return json.dumps({**json.loads(packet), **changes}).encode()


def test_success_and_increasing_sequence(protocol, lifetime):
    state = session(protocol, lifetime)
    first = state.challenge(10)
    reply = protocol.respond(first, lifetime, 60)
    state.accept(reply, 110)  # Both inclusive duration limits are exercised.
    second = state.challenge(111)
    assert json.loads(second)["sequence"] == 2
    assert len(first) <= protocol.PACKET_BYTES
    assert len(reply) <= protocol.PACKET_BYTES
    assert json.loads(first)["token"] != json.loads(second)["token"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("boot_id", "33333333-3333-3333-3333-333333333333"),
        ("collection_id", "33333333-3333-3333-3333-333333333333"),
        ("container_id", "f" * 64),
        ("owner", "f" * 32),
        ("watchdog_pid", 124),
        ("watchdog_start_ticks", 457),
        ("deadline_ns", 10001),
        ("token", "f" * 64),
        ("sequence", 2),
        ("sent_ns", 9),
        ("version", True),
        ("kind", "challenge"),
        ("extra", 1),
        ("generated_ns", True),
    ],
)
def test_mismatch_latches_failure(protocol, lifetime, field, value):
    state = session(protocol, lifetime)
    reply = protocol.respond(state.challenge(10), lifetime, 20)
    with pytest.raises(protocol.HealthError):
        state.accept(mutated(reply, **{field: value}), 30)
    with pytest.raises(protocol.HealthError, match="SESSION_FAILED"):
        state.challenge(31)


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"x" * 2049,
        b"null",
        b"[]",
        b"{}",
        b"\xff",
        b'{"version":1,"version":1}',
        b"[" * 1000,
        b'{"x":NaN}',
    ],
)
def test_malformed_packet_latches_failure(protocol, lifetime, packet):
    state = session(protocol, lifetime)
    state.challenge(10)
    with pytest.raises(protocol.HealthError):
        state.accept(packet, 20)
    with pytest.raises(protocol.HealthError, match="SESSION_FAILED"):
        state.check(21)


@pytest.mark.parametrize("generated,received", [(9, 30), (31, 30), (20, 71), (60, 111)])
def test_time_failure(protocol, lifetime, generated, received):
    state = session(protocol, lifetime)
    reply = protocol.respond(state.challenge(10), lifetime, 20)
    with pytest.raises(protocol.HealthError):
        state.accept(mutated(reply, generated_ns=generated), received)


def test_replay_and_no_reopening(protocol, lifetime):
    state = session(protocol, lifetime)
    reply = protocol.respond(state.challenge(10), lifetime, 20)
    state.accept(reply, 30)
    state.challenge(40)
    with pytest.raises(protocol.HealthError, match="EXCHANGE_MISMATCH"):
        state.accept(reply, 50)
    with pytest.raises(protocol.HealthError, match="SESSION_FAILED"):
        state.accept(reply, 60)


def test_duplicate_reply(protocol, lifetime):
    state = session(protocol, lifetime)
    reply = protocol.respond(state.challenge(10), lifetime, 20)
    state.accept(reply, 30)
    with pytest.raises(protocol.HealthError, match="NO_OUTSTANDING"):
        state.accept(reply, 31)


def test_second_outstanding_challenge(protocol, lifetime):
    state = session(protocol, lifetime)
    state.challenge(10)
    with pytest.raises(protocol.HealthError, match="CHALLENGE_OUTSTANDING"):
        state.challenge(11)


@pytest.mark.parametrize("now", [-1, True, 10.0, float("nan"), 10000, 10001])
def test_bad_clock(protocol, lifetime, now):
    with pytest.raises(protocol.HealthError):
        session(protocol, lifetime).challenge(now)


def test_clock_reversal_and_timeout_without_reply(protocol, lifetime):
    for now in (9, 111):
        state = session(protocol, lifetime)
        state.challenge(10)
        with pytest.raises(protocol.HealthError):
            state.check(now)
        with pytest.raises(protocol.HealthError, match="SESSION_FAILED"):
            state.challenge(112)


@pytest.mark.parametrize(
    "timeout,age", [(0, 1), (100, 0), (100, 101), (10000, 1), (True, 1)]
)
def test_invalid_limits(protocol, lifetime, timeout, age):
    with pytest.raises(protocol.HealthError, match="INVALID_CONFIGURATION"):
        protocol.Session(
            lifetime,
            response_timeout_ns=timeout,
            max_generation_age_ns=age,
            token_factory=lambda: "c" * 64,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("boot_id", "bad"),
        ("collection_id", "BAD"),
        ("container_id", "A" * 64),
        ("owner", "short"),
        ("watchdog_pid", True),
        ("watchdog_start_ticks", 0),
        ("deadline_ns", -1),
    ],
)
def test_bad_lifetime(protocol, lifetime, field, value):
    with pytest.raises(protocol.HealthError, match="INVALID_LIFETIME"):
        session(protocol, replace(lifetime, **{field: value}))


def test_responder_rejects_changed_identity_and_deadline(protocol, lifetime):
    challenge = session(protocol, lifetime).challenge(10)
    with pytest.raises(protocol.HealthError, match="LIFETIME_MISMATCH"):
        protocol.respond(challenge, replace(lifetime, watchdog_pid=999), 20)
    for now in (9, 10000, True):
        with pytest.raises(protocol.HealthError, match="INVALID_TIME"):
            protocol.respond(challenge, lifetime, now)


def test_token_failure_and_reuse(protocol, lifetime):
    for factory in (lambda: "bad", lambda: (_ for _ in ()).throw(OSError())):
        state = protocol.Session(
            lifetime,
            response_timeout_ns=100,
            max_generation_age_ns=50,
            token_factory=factory,
        )
        with pytest.raises(protocol.HealthError):
            state.challenge(10)
        with pytest.raises(protocol.HealthError, match="SESSION_FAILED"):
            state.challenge(11)
    state = protocol.Session(
        lifetime,
        response_timeout_ns=100,
        max_generation_age_ns=50,
        token_factory=lambda: "c" * 64,
    )
    state.accept(protocol.respond(state.challenge(10), lifetime, 20), 30)
    with pytest.raises(protocol.HealthError, match="INVALID_TOKEN"):
        state.challenge(40)


def test_unsolicited_response_fails(protocol, lifetime):
    reply = protocol.respond(session(protocol, lifetime).challenge(10), lifetime, 20)
    with pytest.raises(protocol.HealthError, match="NO_OUTSTANDING"):
        session(protocol, lifetime).accept(reply, 30)


def test_exact_packet_size_and_one_byte_over(protocol, lifetime):
    for size, succeeds in ((2048, True), (2049, False)):
        state = session(protocol, lifetime)
        reply = protocol.respond(state.challenge(10), lifetime, 20)
        padded = reply + b" " * (size - len(reply))
        if succeeds:
            state.accept(padded, 30)
        else:
            with pytest.raises(protocol.HealthError, match="INVALID_PACKET"):
                state.accept(padded, 30)


def test_missing_fields_rejected(protocol, lifetime):
    template = protocol.respond(session(protocol, lifetime).challenge(10), lifetime, 20)
    for field in json.loads(template):
        state = session(protocol, lifetime)
        state.challenge(10)
        message = json.loads(template)
        del message[field]
        with pytest.raises(protocol.HealthError, match="INVALID_PACKET"):
            state.accept(json.dumps(message).encode(), 30)


def test_duplicate_key_and_boolean_identity_rejected(protocol, lifetime):
    state = session(protocol, lifetime)
    reply = protocol.respond(state.challenge(10), lifetime, 20)
    duplicate = b'{"version":1,' + reply[1:]
    with pytest.raises(protocol.HealthError, match="INVALID_PACKET"):
        state.accept(duplicate, 30)
    state = session(protocol, lifetime)
    reply = protocol.respond(state.challenge(10), lifetime, 20)
    with pytest.raises(protocol.HealthError, match="EXCHANGE_MISMATCH"):
        state.accept(mutated(reply, watchdog_pid=True), 30)
