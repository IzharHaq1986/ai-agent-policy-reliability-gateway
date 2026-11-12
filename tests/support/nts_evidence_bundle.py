"""Bound a diagnostic NTS evidence envelope without performing any external I/O."""

import base64
import json
import math
import re
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from nts_command_capture import BUNDLE_BYTES, QUERY_BYTES

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)

MAX_BYTES = BUNDLE_BYTES
MAX_OBSERVATIONS = 128
MAX_NODES = 4096
MAX_DEPTH = 8
MAX_TEXT_BYTES = 32768
MAX_MEMBERS = 128
_RESERVED = {"schema_version", "measurement_age", "approval_eligibility"}
_REPORT_KEYS = {"collection_id", "metadata", "observations"}
_OBSERVATION_KEYS = {
    "query_ref",
    "stdout",
    "stderr",
    "returncode",
    "reason_code",
    "started_monotonic",
    "ended_monotonic",
}


class EvidenceError(ValueError):
    """Expose fixed failure codes, never supplied observations or object details."""


@dataclass
class _InputBudget:
    nodes: int = MAX_NODES
    remaining: int = MAX_BYTES

    def node(self) -> None:
        self.nodes -= 1
        if self.nodes < 0:
            raise EvidenceError("INPUT_LIMIT")

    def charge(self, size: int) -> None:
        if size > self.remaining:
            raise EvidenceError("INPUT_LIMIT")
        self.remaining -= size


def _fields(value: object, keys: set[str]) -> dict[str, object]:
    # Exact builtin types prevent custom mapping and encoder callbacks.
    if (
        type(value) is not dict
        or len(value) != len(keys)
        or any(type(key) is not str for key in value)
    ):
        raise EvidenceError("INVALID_REPORT")
    if value.keys() != keys:
        raise EvidenceError("INVALID_REPORT")
    return cast(dict[str, object], value)


def _text(value: object, budget: _InputBudget, limit: int = MAX_TEXT_BYTES) -> str:
    if type(value) is not str or len(value) > limit:
        raise EvidenceError("INVALID_TEXT")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise EvidenceError("INVALID_TEXT") from None
    if len(encoded) > limit:
        raise EvidenceError("INPUT_LIMIT")
    budget.charge(len(encoded))
    return value


def _plain(
    value: object, budget: _InputBudget, active: set[int], depth: int = 0
) -> JsonValue:
    """Validate and copy bounded plain metadata before starting JSON encoding."""
    budget.node()
    if depth > MAX_DEPTH:
        raise EvidenceError("INPUT_LIMIT")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -(1 << 63) <= value < (1 << 63):
            raise EvidenceError("INVALID_NUMBER")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise EvidenceError("INVALID_NUMBER")
        return value
    if type(value) is str:
        return _text(value, budget)
    if type(value) not in (dict, list):
        raise EvidenceError("INVALID_METADATA")
    if (
        len(cast(dict[object, object] | list[object], value)) > MAX_MEMBERS
        or id(value) in active
    ):
        raise EvidenceError("INPUT_LIMIT")
    active.add(id(value))
    try:
        if type(value) is list:
            return [_plain(item, budget, active, depth + 1) for item in value]
        result: dict[str, JsonValue] = {}
        for key, item in cast(dict[object, object], value).items():
            key = _text(key, budget, 64)
            if not key or key in _RESERVED:
                raise EvidenceError("RESERVED_OR_INVALID_METADATA_KEY")
            result[key] = _plain(item, budget, active, depth + 1)
        return result
    finally:
        active.remove(id(value))


def _timestamp(value: object, budget: _InputBudget) -> int | float:
    if type(value) not in (int, float):
        raise EvidenceError("INVALID_OBSERVATION")
    checked = _plain(value, budget, set())
    if type(checked) not in (int, float) or cast(int | float, checked) < 0:
        raise EvidenceError("INVALID_OBSERVATION")
    return cast(int | float, checked)


def _observation(value: object, budget: _InputBudget) -> dict[str, JsonValue]:
    budget.node()
    fields = _fields(value, _OBSERVATION_KEYS)
    query_ref = _text(fields["query_ref"], budget, 64)
    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", query_ref) is None:
        raise EvidenceError("INVALID_OBSERVATION")
    stdout, stderr = fields["stdout"], fields["stderr"]
    if type(stdout) is not bytes or type(stderr) is not bytes:
        raise EvidenceError("INVALID_OBSERVATION")
    if len(stdout) + len(stderr) > QUERY_BYTES:
        raise EvidenceError("INPUT_LIMIT")
    budget.charge(len(stdout) + len(stderr))
    returncode = fields["returncode"]
    if returncode is not None and (
        type(returncode) is not int or not -255 <= returncode <= 255
    ):
        raise EvidenceError("INVALID_OBSERVATION")
    reason = fields["reason_code"]
    if reason is not None:
        reason = _text(reason, budget, 64)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", reason) is None:
            raise EvidenceError("INVALID_OBSERVATION")
    started = _timestamp(fields["started_monotonic"], budget)
    ended = _timestamp(fields["ended_monotonic"], budget)
    if ended < started:
        raise EvidenceError("INVALID_OBSERVATION")
    return {
        "query_ref": query_ref,
        "stdout_base64": base64.b64encode(stdout).decode("ascii"),
        "stderr_base64": base64.b64encode(stderr).decode("ascii"),
        "returncode": returncode,
        "reason_code": reason,
        "started_monotonic": started,
        "ended_monotonic": ended,
    }


def serialize_evidence(report: object, *, byte_limit: int = MAX_BYTES) -> bytes:
    """Return one complete newline-terminated report, or fail without output."""
    if type(byte_limit) is not int or not 0 < byte_limit <= MAX_BYTES:
        raise EvidenceError("INVALID_BYTE_LIMIT")
    fields = _fields(report, _REPORT_KEYS)
    budget = _InputBudget()
    collection = _text(fields["collection_id"], budget, 36)
    try:
        canonical = str(UUID(collection))
    except ValueError:
        raise EvidenceError("INVALID_COLLECTION_ID") from None
    if canonical != collection:
        raise EvidenceError("INVALID_COLLECTION_ID")
    if type(fields["metadata"]) is not dict:
        raise EvidenceError("INVALID_METADATA")
    metadata = _plain(fields["metadata"], budget, set())
    observations = fields["observations"]
    if type(observations) is not list or len(observations) > MAX_OBSERVATIONS:
        raise EvidenceError("INVALID_OBSERVATIONS")
    envelope = {
        "schema_version": 1,
        "collection_id": collection,
        "measurement_age": "unknown",
        "approval_eligibility": "blocked",
        "stream_encoding": "base64",
        "metadata": metadata,
        "observations": [_observation(item, budget) for item in observations],
    }
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    encoded = bytearray()
    # Field bounds cap individual encoder chunks. Never build an unrestricted
    # full JSON string or return a successful prefix when the final limit fails.
    for chunk in encoder.iterencode(envelope):
        fragment = chunk.encode("utf-8")
        if len(fragment) > byte_limit - len(encoded) - 1:
            raise EvidenceError("OUTPUT_LIMIT")
        encoded.extend(fragment)
    encoded.extend(b"\n")
    return bytes(encoded)
