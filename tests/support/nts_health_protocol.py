"""Pure bounded health exchanges; supplied identities and times are not attested."""

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import NoReturn

PACKET_BYTES = 2048
MAX_INTEGER = (1 << 63) - 1


class HealthError(RuntimeError):
    """Expose fixed reasons without echoing private message contents."""


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= MAX_INTEGER


def _hex(value: object, length: int) -> bool:
    return (
        type(value) is str and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None
    )


@dataclass(frozen=True)
class Lifetime:
    """Trusted launcher references; matching fields do not verify a process."""

    boot_id: str
    collection_id: str
    container_id: str
    owner: str
    watchdog_pid: int
    watchdog_start_ticks: int
    deadline_ns: int


def _lifetime(value: Lifetime) -> None:
    if type(value) is not Lifetime:
        raise HealthError("INVALID_LIFETIME")
    for reference in (value.boot_id, value.collection_id):
        if (
            type(reference) is not str
            or re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", reference)
            is None
        ):
            raise HealthError("INVALID_LIFETIME")
    if not (
        _hex(value.container_id, 64)
        and _hex(value.owner, 32)
        and _integer(value.watchdog_pid, 1)
        and _integer(value.watchdog_start_ticks, 1)
        and _integer(value.deadline_ns, 1)
    ):
        raise HealthError("INVALID_LIFETIME")


def _encode(message: dict[str, object]) -> bytes:
    encoded = json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > PACKET_BYTES:
        raise HealthError("PACKET_LIMIT")
    return encoded


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HealthError("INVALID_PACKET")
        result[key] = value
    return result


def _decode(packet: bytes, kind: str) -> dict[str, object]:
    if type(packet) is not bytes or not 0 < len(packet) <= PACKET_BYTES:
        raise HealthError("INVALID_PACKET")
    try:
        message = json.loads(packet, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise HealthError("INVALID_PACKET") from None
    fields = set(Lifetime.__dataclass_fields__) | {
        "version",
        "kind",
        "token",
        "sequence",
        "sent_ns",
    }
    if kind == "response":
        fields.add("generated_ns")
    if type(message) is not dict or set(message) != fields:
        raise HealthError("INVALID_PACKET")
    if (
        type(message["version"]) is not int
        or message["version"] != 1
        or message["kind"] != kind
        or not _hex(message["token"], 64)
        or not _integer(message["sequence"], 1)
        or not _integer(message["sent_ns"])
        or (kind == "response" and not _integer(message["generated_ns"]))
    ):
        raise HealthError("INVALID_PACKET")
    # Compare wire fields against a separately validated trusted lifetime below.
    return message


def respond(challenge: bytes, lifetime: Lifetime, generated_ns: int) -> bytes:
    """Construct a reply only; no channel, process, or watchdog action occurs."""
    _lifetime(lifetime)
    message = _decode(challenge, "challenge")
    if any(
        type(message[k]) is not type(v) or message[k] != v
        for k, v in asdict(lifetime).items()
    ):
        raise HealthError("LIFETIME_MISMATCH")
    sent = message["sent_ns"]
    assert isinstance(sent, int)
    if not _integer(generated_ns) or not sent <= generated_ns < lifetime.deadline_ns:
        raise HealthError("INVALID_TIME")
    return _encode({**message, "kind": "response", "generated_ns": generated_ns})


class Session:
    """Single-owner protocol state; any exchange failure permanently latches failure.

    Trusted code supplies an unpredictable 256-bit token generator and monotonic
    nanoseconds. This class neither obtains nor verifies their provenance.
    """

    def __init__(
        self,
        lifetime: Lifetime,
        *,
        response_timeout_ns: int,
        max_generation_age_ns: int,
        token_factory: Callable[[], str],
    ) -> None:
        _lifetime(lifetime)
        if not (
            _integer(response_timeout_ns, 1)
            and _integer(max_generation_age_ns, 1)
            and max_generation_age_ns <= response_timeout_ns < lifetime.deadline_ns
            and callable(token_factory)
        ):
            raise HealthError("INVALID_CONFIGURATION")
        self._lifetime = lifetime
        self._timeout = response_timeout_ns
        self._age = max_generation_age_ns
        self._tokens = token_factory
        self._pending: dict[str, object] | None = None
        self._sequence = 0
        self._last_time = 0
        self._previous_token: str | None = None
        self._failed = False

    def _fail(self, reason: str) -> NoReturn:
        self._failed = True
        self._pending = None
        raise HealthError(reason)

    def check(self, now_ns: int) -> None:
        """Trusted supervisor must call this independently while awaiting replies."""
        if self._failed:
            raise HealthError("SESSION_FAILED")
        if (
            not _integer(now_ns)
            or not self._last_time <= now_ns < self._lifetime.deadline_ns
        ):
            self._fail("INVALID_TIME")
        self._last_time = now_ns
        if self._pending is not None:
            sent = self._pending["sent_ns"]
            assert isinstance(sent, int)
            if now_ns - sent > self._timeout:
                self._fail("RESPONSE_TIMEOUT")

    def challenge(self, now_ns: int) -> bytes:
        """Start one exchange; token generation failure cannot reopen the session."""
        self.check(now_ns)
        if self._pending is not None:
            self._fail("CHALLENGE_OUTSTANDING")
        try:
            token = self._tokens()
        except Exception:
            self._fail("TOKEN_UNAVAILABLE")
        if not _hex(token, 64) or token == self._previous_token:
            self._fail("INVALID_TOKEN")
        if self._sequence == MAX_INTEGER:
            self._fail("SEQUENCE_EXHAUSTED")
        self._sequence += 1
        self._previous_token = token
        self._pending = {
            **asdict(self._lifetime),
            "version": 1,
            "kind": "challenge",
            "token": token,
            "sequence": self._sequence,
            "sent_ns": now_ns,
        }
        return _encode(self._pending)

    def accept(self, packet: bytes, received_ns: int) -> None:
        """Validate only this exchange; returning conveys no execution authority."""
        self.check(received_ns)
        if self._pending is None:
            self._fail("NO_OUTSTANDING_CHALLENGE")
        try:
            message = _decode(packet, "response")
        except HealthError:
            self._fail("INVALID_PACKET")
        assert self._pending is not None
        expected = {**self._pending, "kind": "response"}
        if any(
            type(message[k]) is not type(v) or message[k] != v
            for k, v in expected.items()
        ):
            self._fail("EXCHANGE_MISMATCH")
        generated = message["generated_ns"]
        sent = self._pending["sent_ns"]
        assert isinstance(generated, int) and isinstance(sent, int)
        if not sent <= generated <= received_ns or received_ns - generated > self._age:
            self._fail("INVALID_GENERATION_TIME")
        self._pending = None
