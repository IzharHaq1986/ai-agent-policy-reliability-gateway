"""Pure bootstrap syntax checks; no transport, child, Docker, or time-source calls."""

import importlib  # Load only the explicitly selected local support module.
import json  # Construct independent malformed and alternative wire encodings.
import re  # Extract the published schema examples for compatibility checks.
from dataclasses import (
    FrozenInstanceError,
    asdict,
    replace,
)  # Build controlled test values.
from pathlib import Path  # Locate reviewed helpers and documentation deterministically.

import pytest  # Restore injected faults and express exact rejection boundaries.


@pytest.fixture
def codec(monkeypatch):
    """Importing the codec must not start any workload or exchange."""
    monkeypatch.syspath_prepend(
        str(Path(__file__).parent / "support")
    )  # Fixed helper path.
    return importlib.import_module("nts_bootstrap_codec")  # No caller-selected module.


@pytest.fixture
def context(codec):
    """Use documented synthetic values, never runtime identities or credentials."""
    return codec.BootstrapContext(
        codec.Lifetime(  # These assertions do not refer to a real container or child.
            boot_id="11111111-1111-4111-8111-111111111111",
            collection_id="22222222-2222-4222-8222-222222222222",
            container_id="d" * 64,  # Full format, not evidence of actual ownership.
            owner="c" * 32,  # Synthetic label, not an authentication credential.
            watchdog_pid=4242,  # Arbitrary positive example reference.
            watchdog_start_ticks=9000,  # No kernel lookup accompanies this number.
            # Representation alone cannot establish freshness.
            deadline_ns=100000000000,
        ),
        "e" * 64,  # Deliberately predictable example token, not production entropy.
    )


def packet(context, kind="bootstrap_context"):
    """Construct wire data independently of the codec serializer."""
    return {
        **asdict(context.lifetime),  # Preserve the documented lifetime field values.
        "version": 1,  # Independently pin the bootstrap schema version.
        "kind": kind,  # Tests deliberately vary message purpose where needed.
        "token": context.token,  # Bind this sample to its separately supplied context.
    }


def consume(codec, context, raw, kind):
    """Exercise each public decoding boundary with the same adversarial input."""
    if kind == "bootstrap_context":  # Context decoding returns assertions, not trust.
        return codec.decode_context(raw)
    return codec.verify_acknowledgement(
        raw, context
    )  # Compare with separate expectations.


def test_documented_examples_and_deterministic_round_trip(codec, context):
    # Read the contract rather than deriving expected bytes from the implementation.
    text = (Path(__file__).parents[1] / "docs/nts-container-watchdog.md").read_text()
    section = text.split("## Future bootstrap context and acknowledgement contract", 1)[
        1
    ]
    examples = re.findall(
        r"```json\n(.*?)\n```", section, re.S
    )  # Exactly two published packets.
    assert (
        len(examples) == 2
    )  # Require independent context and acknowledgement examples.
    assert (
        codec.encode_context(context) == examples[0].encode()
    )  # Canonical exact bytes.
    assert (
        codec.encode_acknowledgement(context) == examples[1].encode()
    )  # Exact receipt.
    assert codec.encode_context(context) == codec.encode_context(
        context
    )  # No new state.
    decoded = codec.decode_context(
        examples[0].encode()
    )  # Exercise actual strict parsing.
    assert decoded == context  # Every lifetime field and token survives unchanged.
    assert (
        codec.verify_acknowledgement(examples[1].encode(), context) is None
    )  # Equality only.
    with pytest.raises(
        FrozenInstanceError
    ):  # Ordinary mutation cannot change a decoded card.
        # Attempt ordinary mutation, not privileged memory tampering.
        decoded.token = "f" * 64
    with pytest.raises(
        FrozenInstanceError
    ):  # The nested lifetime is immutable as well.
        # The nested value rejects normal assignment.
        decoded.lifetime.watchdog_pid = 9999


@pytest.mark.parametrize("kind", ["bootstrap_context", "bootstrap_ack"])
def test_packet_limit_and_noncanonical_json_order(codec, context, kind):
    raw = json.dumps(
        packet(context, kind), indent=1
    ).encode()  # Valid spacing is permitted.
    padded = (
        b" " * (2048 - len(raw)) + raw
    )  # Include whitespace in the complete byte limit.
    result = consume(codec, context, padded, kind)  # Exactly 2 KiB remains valid JSON.
    assert result == (context if kind == "bootstrap_context" else None)  # No coercion.
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        consume(
            codec, context, b" " + padded, kind
        )  # One byte over the whole-packet cap.


@pytest.mark.parametrize("kind", ["bootstrap_context", "bootstrap_ack"])
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b"null",
        b"[]",
        b"true",
        b"1",  # Invalid roots or incomplete JSON.
        b"{} trailing",
        b"{}{}",  # Do not accept a valid prefix or multiple packets.
        b"\xff",
        b"\xef\xbb\xbf{}",  # Invalid UTF-8 and forbidden BOM.
        "{}".encode("utf-16"),  # Do not auto-detect another wire encoding.
        b"[" * 1100 + b"]" * 900,  # Bound and reject recursive/incomplete structures.
        b"x" * 2049,  # Reject oversize before attempting a permissive parse.
    ],
)
def test_bad_wire_rejected(codec, context, kind, raw):
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        consume(
            codec, context, raw, kind
        )  # Both boundaries enforce complete bounded input.


@pytest.mark.parametrize(
    "value", [None, "{}", bytearray(b"{}"), memoryview(b"{}"), True]
)
def test_exact_bytes_input_required(codec, context, value):
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        codec.decode_context(value)  # Never coerce a foreign object to a wire packet.
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        codec.verify_acknowledgement(
            value, context
        )  # Receipt boundary is equally strict.


@pytest.mark.parametrize("kind", ["bootstrap_context", "bootstrap_ack"])
@pytest.mark.parametrize(
    "field",
    [
        "version",
        "kind",
        "token",
        "boot_id",
        "collection_id",
        "container_id",
        "owner",
        "watchdog_pid",
        "watchdog_start_ticks",
        "deadline_ns",
    ],
)
def test_missing_and_duplicate_keys_rejected(codec, context, kind, field):
    value = packet(context, kind)  # Start from an independently generated valid schema.
    missing = dict(value)  # Preserve the baseline for the separate duplicate-key case.
    missing.pop(field)  # Missing any one contract field must fail.
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        consume(codec, context, json.dumps(missing).encode(), kind)
    raw = json.dumps(
        value
    )  # Append the same key and same value, not merely a mismatch.
    duplicate = (
        raw[:-1] + "," + json.dumps(field) + ":" + json.dumps(value[field]) + "}"
    )
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        consume(
            codec, context, duplicate.encode(), kind
        )  # Equal duplicates still fail.


@pytest.mark.parametrize("kind", ["bootstrap_context", "bootstrap_ack"])
@pytest.mark.parametrize("value", [0, 2, True, 1.0, "1", None, [], {}])
def test_wrong_version_rejected(codec, context, kind, value):
    wire = packet(
        context, kind
    )  # Version is independently checked before field construction.
    wire["version"] = (
        value  # Booleans and numeric equality cannot stand in for integer 1.
    )
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        consume(codec, context, json.dumps(wire).encode(), kind)


@pytest.mark.parametrize("kind", ["bootstrap_context", "bootstrap_ack"])
@pytest.mark.parametrize(
    "field",
    [
        "boot_id",
        "collection_id",
        "container_id",
        "owner",
        "watchdog_pid",
        "watchdog_start_ticks",
        "deadline_ns",
        "token",
    ],
)
@pytest.mark.parametrize("value", [None, True, [], {}, 1.5])
def test_invalid_field_types_rejected(codec, context, kind, field, value):
    wire = packet(context, kind)  # Keep every unselected field valid.
    wire[field] = value  # Exercise nested objects, arrays, booleans, and non-integers.
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        consume(codec, context, json.dumps(wire).encode(), kind)


@pytest.mark.parametrize(
    "field", ["watchdog_pid", "watchdog_start_ticks", "deadline_ns"]
)
@pytest.mark.parametrize("value", [-1, 0, 2**63, "1", float("inf"), float("nan")])
def test_integer_bounds_rejected(codec, context, field, value):
    wire = packet(
        context
    )  # JSON's permissive non-finite constants must not slip through.
    wire[field] = value  # Enforce positive signed-63-bit integer semantics.
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        codec.decode_context(json.dumps(wire).encode())


@pytest.mark.parametrize(
    "field", ["watchdog_pid", "watchdog_start_ticks", "deadline_ns"]
)
@pytest.mark.parametrize("value", [1, 2**63 - 1])
def test_integer_boundaries_round_trip(codec, context, field, value):
    lifetime = replace(
        context.lifetime, **{field: value}
    )  # Boundary remains a real integer.
    expected = replace(
        context, lifetime=lifetime
    )  # Keep token and other references fixed.
    assert codec.decode_context(codec.encode_context(expected)) == expected
    assert (
        codec.verify_acknowledgement(codec.encode_acknowledgement(expected), expected)
        is None
    )


@pytest.mark.parametrize(
    "field,length", [("container_id", 64), ("owner", 32), ("token", 64)]
)
@pytest.mark.parametrize(
    "fault", ["short", "long", "uppercase", "nonhex", "space", "unicode"]
)
def test_hex_reference_formats_rejected(codec, context, field, length, fault):
    values = {  # Format failure must not be normalized into an accepted reference.
        "short": "a" * (length - 1),
        "long": "a" * (length + 1),
        "uppercase": "A" * length,
        "nonhex": "g" * length,
        "space": "a" * (length - 1) + " ",
        "unicode": "a" * (length - 1) + "é",
    }
    wire = packet(context)  # Independently construct the candidate context.
    wire[field] = values[fault]  # Exercise exact lowercase ASCII syntax.
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        codec.decode_context(json.dumps(wire, ensure_ascii=False).encode())


@pytest.mark.parametrize("field", ["boot_id", "collection_id"])
@pytest.mark.parametrize("value", ["A" * 36, "0" * 36, "", "1-1-1-1-1"])
def test_uuid_shape_rejected(codec, context, field, value):
    wire = packet(context)  # Test the existing shape without inventing UUID semantics.
    wire[field] = value  # Wrong alphabet, separator placement, or length must fail.
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        codec.decode_context(json.dumps(wire).encode())


@pytest.mark.parametrize(
    "field,value",
    [
        ("boot_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("collection_id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("container_id", "a" * 64),
        ("owner", "a" * 32),
        ("watchdog_pid", 4243),
        ("watchdog_start_ticks", 9001),
        ("deadline_ns", 100000000001),
        ("token", "f" * 64),
    ],
)
def test_each_valid_but_mismatched_receipt_field_rejected(codec, context, field, value):
    wire = packet(
        context, "bootstrap_ack"
    )  # Keep the acknowledgement structurally valid.
    wire[field] = (
        value  # Mismatch exactly one field against separate trusted expectations.
    )
    with pytest.raises(codec.BootstrapError, match="^ACKNOWLEDGEMENT_MISMATCH$"):
        codec.verify_acknowledgement(json.dumps(wire).encode(), context)


@pytest.mark.parametrize("field,value", [("token", "bad"), ("watchdog_pid", True)])
def test_invalid_caller_context_rejected(codec, context, field, value):
    invalid = (  # Caller construction does not bypass boundary validation.
        replace(context, token=value)
        if field == "token"
        else replace(context, lifetime=replace(context.lifetime, **{field: value}))
    )
    with pytest.raises(codec.BootstrapError, match="^INVALID_CONTEXT$"):
        codec.encode_context(invalid)
    with pytest.raises(codec.BootstrapError, match="^INVALID_CONTEXT$"):
        codec.encode_acknowledgement(invalid)
    with pytest.raises(codec.BootstrapError, match="^INVALID_CONTEXT$"):
        codec.verify_acknowledgement(codec.encode_acknowledgement(context), invalid)


def test_extra_fields_and_namespace_separation(codec, context):
    wire = packet(
        context
    )  # A command cannot be smuggled through an extra bootstrap key.
    wire["command"] = (
        "DO_NOT_EXECUTE"  # No execution or configuration field is supported.
    )
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        codec.decode_context(json.dumps(wire).encode())
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        codec.decode_context(codec.encode_acknowledgement(context))  # Wrong phase/kind.
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        codec.verify_acknowledgement(codec.encode_context(context), context)
    health = importlib.import_module(
        "nts_health_protocol"
    )  # Use the actual existing helper.
    session = (
        health.Session(  # Supply synthetic monotonic values without reading a clock.
            context.lifetime,
            response_timeout_ns=10,
            max_generation_age_ns=10,
            token_factory=lambda: "f" * 64,
        )
    )
    challenge = session.challenge(
        1
    )  # Valid health packets belong to another namespace.
    response = health.respond(
        challenge, context.lifetime, 2
    )  # No actual responder runs.
    for raw in (
        challenge,
        response,
    ):  # Neither health purpose can be bootstrap context.
        with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
            codec.decode_context(raw)
    with pytest.raises(health.HealthError, match="^INVALID_PACKET$"):
        health.respond(codec.encode_context(context), context.lifetime, 2)
    with pytest.raises(health.HealthError, match="^INVALID_PACKET$"):
        session.accept(codec.encode_acknowledgement(context), 3)


def test_syntax_and_equality_do_not_claim_freshness_or_replay_protection(
    codec, context
):
    value = replace(
        context, lifetime=replace(context.lifetime, deadline_ns=1), token="0" * 64
    )
    decoded = codec.decode_context(
        codec.encode_context(value)
    )  # Positive syntax is valid.
    assert decoded == value  # No clock, entropy, origin, or ownership claim follows.
    raw = codec.encode_acknowledgement(value)  # This does not prove child adoption.
    assert (
        codec.verify_acknowledgement(raw, value) is None
    )  # Only equality is established.
    assert (
        codec.verify_acknowledgement(raw, value) is None
    )  # Future state must reject replay.


def test_errors_do_not_echo_payload(codec, context, capsys):
    wire = packet(
        context
    )  # Include a synthetic private marker in deliberately invalid data.
    wire["token"] = (
        "PRIVATE_PAYLOAD_MUST_NOT_ESCAPE"  # Not a real credential or reference.
    )
    with pytest.raises(codec.BootstrapError) as error:
        codec.decode_context(json.dumps(wire).encode())
    assert str(error.value) == "INVALID_PACKET"  # Keep parse and field details private.
    assert capsys.readouterr() == ("", "")  # The codec performs no diagnostic printing.


@pytest.mark.parametrize("kind", ["bootstrap_context", "bootstrap_ack"])
@pytest.mark.parametrize("encoding", ["utf8_bom", "utf16", "utf32", "invalid_utf8"])
def test_alternate_encoding_of_otherwise_valid_packet_rejected(
    codec, context, kind, encoding
):
    # Otherwise valid schema must not mask encoding bugs.
    value = json.dumps(packet(context, kind))
    # JSON must not auto-detect another encoding or ignore an invalid suffix.
    candidates = {
        "utf8_bom": b"\xef\xbb\xbf" + value.encode(),
        "utf16": value.encode("utf-16"),
        "utf32": value.encode("utf-32"),
        "invalid_utf8": value.encode() + b"\xff",
    }
    # Isolate encoding rejection from the size limit.
    assert len(candidates[encoding]) <= 2048
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        consume(codec, context, candidates[encoding], kind)


@pytest.mark.parametrize("kind", ["bootstrap_context", "bootstrap_ack"])
def test_escaped_duplicate_key_rejected(codec, context, kind):
    raw = json.dumps(packet(context, kind))  # Keep the original valid token field.
    duplicate = raw[:-1] + ',"to\\u006ben":' + json.dumps(context.token) + "}"
    with pytest.raises(codec.BootstrapError, match="^INVALID_PACKET$"):
        # Escaping cannot hide key equality.
        consume(codec, context, duplicate.encode(), kind)


@pytest.mark.parametrize("value", [None, {}, True, "context"])
def test_foreign_expected_context_and_encoding_inputs_rejected(codec, context, value):
    with pytest.raises(codec.BootstrapError, match="^INVALID_CONTEXT$"):
        codec.encode_context(value)  # Do not read attributes on an arbitrary object.
    with pytest.raises(codec.BootstrapError, match="^INVALID_CONTEXT$"):
        # Receipt construction requires the same boundary.
        codec.encode_acknowledgement(value)
    with pytest.raises(codec.BootstrapError, match="^INVALID_CONTEXT$"):
        codec.verify_acknowledgement(codec.encode_acknowledgement(context), value)
