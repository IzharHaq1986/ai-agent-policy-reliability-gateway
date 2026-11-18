"""Pure bootstrap packet syntax and equality checks; no adoption or authority."""

import json  # Decode only bounded JSON and produce deterministic complete packets.
from dataclasses import asdict, dataclass  # Retain immutable validated decoded values.
from typing import Literal, cast  # Narrow trusted message kinds and parsed field types.

from nts_health_protocol import (  # Reuse existing internal test-support validation.
    PACKET_BYTES,  # Preserve the shared whole-packet ceiling.
    HealthError,  # Normalize shared validation errors without exposing payloads.
    Lifetime,  # Keep the established seven lifetime fields and their semantics.
    _hex,  # Reuse exact lowercase token syntax, not randomness verification.
    _lifetime,  # Preserve existing reference types, formats, and integer bounds.
    _pairs,  # Reject duplicate JSON keys rather than silently replacing values.
)

_FIELDS = frozenset(  # Freeze the version-1 schema independently of unknown wire keys.
    {
        "version",  # Bootstrap version is separate from the health message namespace.
        "kind",  # Context and acknowledgement are never interchangeable packets.
        "token",  # Match one caller-supplied token; do not generate or attest it.
        "boot_id",  # Preserve the expected host-boot association reference.
        "collection_id",  # Preserve the expected synthetic collection reference.
        "container_id",  # Require a full container reference, never a prefix.
        "owner",  # Preserve the ownership label reference without treating it as a key.
        "watchdog_pid",  # Syntax alone does not establish actual process ownership.
        "watchdog_start_ticks",  # Syntax alone does not verify a process lifetime.
        "deadline_ns",  # Validate representation only, never current-time eligibility.
    }
)


class BootstrapError(RuntimeError):
    """Expose fixed reasons without packet contents or operational references."""


@dataclass(frozen=True)
class BootstrapContext:
    """Immutable values; callers must not mistake decoded assertions for trust."""

    lifetime: Lifetime  # Decoding validates syntax, not independent launch provenance.
    token: str  # A valid format does not establish freshness or unpredictability.


def _validate(context: BootstrapContext) -> None:
    """Validate both caller-created values and decoded values at every boundary."""
    if type(context) is not BootstrapContext:  # Reject subclasses and foreign objects.
        raise BootstrapError("INVALID_CONTEXT")  # Never inspect arbitrary attributes.
    try:  # Share the existing exact Lifetime contract without copying its validator.
        _lifetime(context.lifetime)  # This performs no I/O or process observation.
    except HealthError:  # Keep this codec's error boundary fixed and payload-free.
        raise BootstrapError("INVALID_CONTEXT") from None
    if not _hex(context.token, 64):  # Exact type, lowercase alphabet, and token length.
        raise BootstrapError("INVALID_CONTEXT")  # Do not repair malformed tokens.


def _encode(
    context: BootstrapContext, kind: Literal["bootstrap_context", "bootstrap_ack"]
) -> bytes:
    """Serialize only validated values under one fixed internal message kind."""
    _validate(context)  # Reject invalid inputs before serialization accesses fields.
    message = {  # Values are scalar and bounded by the shared lifetime validator.
        **asdict(context.lifetime),  # Retain every existing lifetime field unchanged.
        # Emit only this bootstrap schema, never a caller-selected version.
        "version": 1,
        "kind": kind,  # The public wrapper selects the permitted message purpose.
        "token": context.token,  # Echo supplied context without generating new state.
    }
    if set(message) != _FIELDS:  # Fail closed if the shared model later changes shape.
        raise BootstrapError("INVALID_CONTEXT")  # Do not silently extend version 1.
    packet = json.dumps(  # Produce one deterministic whole UTF-8 packet.
        message, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")  # Validated reference strings are ASCII-compatible.
    if len(packet) > PACKET_BYTES:  # Keep the complete serialized packet within bounds.
        raise BootstrapError(
            "INVALID_PACKET"
        )  # Never truncate evidence to make it fit.
    return packet  # Encoding conveys no delivery, acknowledgement, or permission.


def _decode(
    packet: bytes, kind: Literal["bootstrap_context", "bootstrap_ack"]
) -> BootstrapContext:
    """Parse a complete packet and return syntax-validated immutable assertions."""
    if type(packet) is not bytes or not 0 < len(packet) <= PACKET_BYTES:
        raise BootstrapError(
            "INVALID_PACKET"
        )  # Reject foreign or oversized inputs first.
    try:  # Decode UTF-8 explicitly so JSON cannot auto-detect another wire encoding.
        message = json.loads(packet.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError, HealthError):
        raise BootstrapError("INVALID_PACKET") from None  # No raw parse errors escape.
    if type(message) is not dict or set(message) != _FIELDS:
        raise BootstrapError("INVALID_PACKET")  # Reject nested roots and schema drift.
    if (  # Booleans, floats, and strings cannot stand in for the version integer.
        type(message["version"]) is not int
        or message["version"] != 1
        or type(message["kind"]) is not str
        or message["kind"] != kind
    ):
        raise BootstrapError("INVALID_PACKET")  # Never dispatch by version alone.
    # Casts guide types only; validation follows immediately.
    context = BootstrapContext(
        lifetime=Lifetime(  # Preserve the seven exact field values without coercion.
            boot_id=cast(str, message["boot_id"]),  # Validate the boot format below.
            collection_id=cast(str, message["collection_id"]),  # No freshness claim.
            container_id=cast(str, message["container_id"]),  # No ownership query.
            owner=cast(str, message["owner"]),  # A label is not an authentication key.
            watchdog_pid=cast(int, message["watchdog_pid"]),  # No PID lookup occurs.
            # Validate the process-start format below without a kernel observation.
            watchdog_start_ticks=cast(int, message["watchdog_start_ticks"]),
            deadline_ns=cast(int, message["deadline_ns"]),  # No clock read occurs.
        ),
        token=cast(str, message["token"]),  # Do not normalize or replace a token.
    )
    try:  # Nothing is returned until actual runtime types and values pass validation.
        _validate(context)  # Reject nested values, incorrect types, and invalid bounds.
    except BootstrapError:
        raise BootstrapError(
            "INVALID_PACKET"
        ) from None  # Preserve the decode boundary.
    return context  # These are assertions, not evidence of actual child adoption.


def encode_context(context: BootstrapContext) -> bytes:
    """Build one context packet without performing delivery or changing state."""
    return _encode(context, "bootstrap_context")  # Fix the context-purpose namespace.


def decode_context(packet: bytes) -> BootstrapContext:
    """Read context assertions; trusted code must separately establish their origin."""
    return _decode(packet, "bootstrap_context")  # Never accept a receipt as context.


def encode_acknowledgement(context: BootstrapContext) -> bytes:
    """Build matching receipt bytes; this does not prove any adoption occurred."""
    return _encode(context, "bootstrap_ack")  # Preserve every supplied context value.


def verify_acknowledgement(packet: bytes, expected: BootstrapContext) -> None:
    """Check syntax and equality against separately supplied expected context only."""
    _validate(
        expected
    )  # The caller must obtain expected values from trusted launch state.
    observed = _decode(
        packet, "bootstrap_ack"
    )  # A context or health packet cannot match.
    if (
        observed != expected
    ):  # All fields have already passed exact runtime type checks.
        raise BootstrapError(
            "ACKNOWLEDGEMENT_MISMATCH"
        )  # Never replace expected context.
    # Returning None conveys equality only, never authority or a continuing lease.
