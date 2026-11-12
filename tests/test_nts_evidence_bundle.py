"""Validate the complete encoded report, not only its raw command output."""

import base64
import copy
import importlib
import json
from pathlib import Path

import pytest

COLLECTION = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    return importlib.import_module("nts_evidence_bundle")


def observation(**changes):
    value = {
        "query_ref": "sources_before",
        "stdout": b"source data",
        "stderr": b"",
        "returncode": 0,
        "reason_code": None,
        "started_monotonic": 100.0,
        "ended_monotonic": 100.2,
    }
    value.update(changes)
    return value


def report(*, metadata=None, observations=None):
    return {
        "collection_id": COLLECTION,
        "metadata": {} if metadata is None else metadata,
        "observations": [observation()] if observations is None else observations,
    }


def test_ordinary_report_has_fixed_diagnostic_envelope(module):
    source = report(
        metadata={"image_id": "sha256:example", "flags": [True, None, 7, -2.0]}
    )
    before = copy.deepcopy(source)
    encoded = module.serialize_evidence(source)
    decoded = json.loads(encoded)
    assert decoded["schema_version"] == 1
    assert decoded["measurement_age"] == "unknown"
    assert decoded["approval_eligibility"] == "blocked"
    assert decoded["stream_encoding"] == "base64"
    assert decoded["collection_id"] == COLLECTION
    assert decoded["metadata"] == source["metadata"]
    assert encoded.endswith(b"\n") and encoded.count(b"\n") == 1
    assert source == before


def test_all_byte_values_are_preserved_in_both_streams(module):
    stdout = bytes(range(256))
    stderr = bytes(reversed(range(256)))
    result = json.loads(
        module.serialize_evidence(
            report(observations=[observation(stdout=stdout, stderr=stderr)])
        )
    )
    saved = result["observations"][0]
    assert base64.b64decode(saved["stdout_base64"], validate=True) == stdout
    assert base64.b64decode(saved["stderr_base64"], validate=True) == stderr


def test_binary_expansion_can_overflow_when_raw_stream_fits(module):
    source = report(observations=[observation(stdout=b"a" * 30000)])
    assert len(source["observations"][0]["stdout"]) < module.QUERY_BYTES
    with pytest.raises(module.EvidenceError, match="^OUTPUT_LIMIT$"):
        module.serialize_evidence(source, byte_limit=32768)


def test_escaping_and_metadata_overhead_are_counted(module):
    source = report(metadata={"text": '"\\\n\x00😀' * 1000})
    encoded = module.serialize_evidence(source)
    assert len(encoded) > len(source["metadata"]["text"].encode())
    assert json.loads(encoded)["metadata"] == source["metadata"]
    with pytest.raises(module.EvidenceError, match="^OUTPUT_LIMIT$"):
        module.serialize_evidence(source, byte_limit=len(encoded) - 1)


def test_complete_envelope_and_trailing_newline_count_at_boundary(module):
    source = report(observations=[])
    expected = module.serialize_evidence(source)
    assert module.serialize_evidence(source, byte_limit=len(expected)) == expected
    with pytest.raises(module.EvidenceError, match="^OUTPUT_LIMIT$"):
        module.serialize_evidence(source, byte_limit=len(expected) - 1)


def test_actual_one_mib_boundary(module):
    # Plain ASCII adds exactly one encoded byte per character. Use separately
    # bounded fields so only the final encoded ceiling decides this boundary.
    source = report(observations=[observation(stdout=b"a" * 30000) for _ in range(25)])
    assert len(module.serialize_evidence(source)) < module.MAX_BYTES
    source["metadata"] = {"pad_a": "", "pad_b": ""}
    needed = module.MAX_BYTES - len(module.serialize_evidence(source))
    assert 0 < needed <= 2 * module.MAX_TEXT_BYTES
    source["metadata"]["pad_a"] = "a" * min(needed, module.MAX_TEXT_BYTES)
    source["metadata"]["pad_b"] = "a" * max(0, needed - module.MAX_TEXT_BYTES)
    assert len(module.serialize_evidence(source)) == module.MAX_BYTES
    source["metadata"]["pad_c"] = ""
    with pytest.raises(module.EvidenceError, match="^OUTPUT_LIMIT$"):
        module.serialize_evidence(source)


def test_aggregate_output_overflow_without_large_individual_records(module):
    source = report(observations=[observation(stdout=b"a" * 30000) for _ in range(27)])
    assert (
        sum(len(item["stdout"]) for item in source["observations"]) < module.MAX_BYTES
    )
    with pytest.raises(module.EvidenceError, match="^OUTPUT_LIMIT$"):
        module.serialize_evidence(source)


def test_deterministic_across_mapping_order_without_reordering_records(module):
    a = report(metadata={"b": {"z": 1, "a": 2}, "a": 3})
    b = dict(reversed(list(report(metadata={"a": 3, "b": {"a": 2, "z": 1}}).items())))
    assert module.serialize_evidence(a) == module.serialize_evidence(b)
    records = [observation(query_ref="first"), observation(query_ref="second")]
    decoded = json.loads(module.serialize_evidence(report(observations=records)))
    assert [item["query_ref"] for item in decoded["observations"]] == [
        "first",
        "second",
    ]


@pytest.mark.parametrize(
    "key", ["schema_version", "measurement_age", "approval_eligibility"]
)
def test_top_level_override_is_rejected(module, key):
    source = report()
    source[key] = "forged"
    with pytest.raises(module.EvidenceError):
        module.serialize_evidence(source)


@pytest.mark.parametrize(
    "key", ["schema_version", "measurement_age", "approval_eligibility"]
)
def test_nested_metadata_cannot_present_reserved_fields(module, key):
    with pytest.raises(module.EvidenceError, match="RESERVED_OR_INVALID_METADATA_KEY"):
        module.serialize_evidence(report(metadata={"nested": {key: "forged"}}))


def test_observed_claim_in_raw_bytes_stays_opaque_and_cannot_override_envelope(module):
    claimed = b'{"approval_eligibility":"allowed","measurement_age":"fresh"}'
    saved = json.loads(
        module.serialize_evidence(report(observations=[observation(stdout=claimed)]))
    )
    assert saved["approval_eligibility"] == "blocked"
    assert saved["measurement_age"] == "unknown"
    assert base64.b64decode(saved["observations"][0]["stdout_base64"]) == claimed


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 2**64])
def test_invalid_metadata_numbers_fail_before_encoding(module, value, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("encoding started before invalid input was rejected")

    monkeypatch.setattr(module.json, "JSONEncoder", forbidden)
    with pytest.raises(module.EvidenceError, match="INVALID_NUMBER"):
        module.serialize_evidence(report(metadata={"value": value}))


@pytest.mark.parametrize(
    "kind",
    ["cycle", "depth", "nodes", "members", "text", "utf8", "raw_total", "record_count"],
)
def test_input_structure_is_bounded_before_encoding(module, kind, monkeypatch):
    source = report()
    if kind == "cycle":
        source["metadata"]["self"] = source["metadata"]
    elif kind == "depth":
        for _ in range(10):
            source["metadata"] = {"child": source["metadata"]}
    elif kind == "nodes":
        source["metadata"] = {str(i): [None] * 128 for i in range(40)}
    elif kind == "members":
        source["metadata"] = {str(i): None for i in range(129)}
    elif kind == "text":
        source["metadata"] = {"value": "x" * (module.MAX_TEXT_BYTES + 1)}
    elif kind == "utf8":
        source["metadata"] = {"value": "😀" * 10000}
    elif kind == "raw_total":
        source["observations"] = [observation(stdout=b"x" * 32768) for _ in range(33)]
    else:
        source["observations"] = [observation() for _ in range(129)]

    def forbidden(**kwargs):
        pytest.fail("encoding started before input bounds were checked")

    monkeypatch.setattr(module.json, "JSONEncoder", forbidden)
    with pytest.raises(module.EvidenceError):
        module.serialize_evidence(source)


@pytest.mark.parametrize(
    "changes",
    [
        {"stdout": "not-bytes"},
        {"stderr": bytearray(b"mutable")},
        {"stdout": b"x" * 20000, "stderr": b"y" * 20000},
        {"returncode": True},
        {"returncode": 999},
        {"reason_code": "unsafe message"},
        {"query_ref": "$(unsafe)"},
        {"started_monotonic": -1},
        {"ended_monotonic": 99},
        {"ended_monotonic": float("nan")},
        {"started_monotonic": True},
        {"extra": "unsupported"},
    ],
)
def test_invalid_observations_are_rejected(module, changes):
    with pytest.raises(module.EvidenceError):
        module.serialize_evidence(report(observations=[observation(**changes)]))


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 1048577])
def test_invalid_output_limit(module, limit):
    with pytest.raises(module.EvidenceError, match="^INVALID_BYTE_LIMIT$"):
        module.serialize_evidence(report(), byte_limit=limit)


@pytest.mark.parametrize(
    "collection",
    ["", "not-a-uuid", COLLECTION.upper().replace("1", "A", 1), "x" * 1000],
)
def test_collection_identity_must_be_canonical(module, collection):
    source = report()
    source["collection_id"] = collection
    with pytest.raises(module.EvidenceError):
        module.serialize_evidence(source)


def test_failure_exposes_no_partial_report_and_has_no_output_side_effect(
    module, capsys, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(module.EvidenceError) as error:
        module.serialize_evidence(
            report(metadata={"secret": "sensitive-content"}), byte_limit=10
        )
    assert str(error.value) == "OUTPUT_LIMIT"
    assert "sensitive" not in str(error.value)
    assert capsys.readouterr() == ("", "")
    assert list(tmp_path.iterdir()) == []


def test_unsupported_objects_never_invoke_callbacks(module):
    callbacks = []

    class Unsupported:
        def __repr__(self):
            callbacks.append("repr")
            raise AssertionError

        def __str__(self):
            callbacks.append("str")
            raise AssertionError

        def __iter__(self):
            callbacks.append("iter")
            raise AssertionError

    with pytest.raises(module.EvidenceError, match="INVALID_METADATA"):
        module.serialize_evidence(report(metadata={"value": Unsupported()}))
    assert callbacks == []


def test_mapping_subclasses_and_lone_surrogates_are_rejected(module):
    class Mapping(dict):
        def items(self):
            pytest.fail("custom mapping callback invoked")

    with pytest.raises(module.EvidenceError):
        module.serialize_evidence(Mapping(report()))
    with pytest.raises(module.EvidenceError):
        module.serialize_evidence(report(metadata=Mapping()))
    with pytest.raises(module.EvidenceError):
        module.serialize_evidence(report(metadata={"value": "\ud800"}))


def test_unavailable_command_status_is_preserved_without_becoming_authority(module):
    source = report(
        observations=[
            observation(
                returncode=None, reason_code="COMMAND_MISSING", stdout=b"", stderr=b""
            )
        ]
    )
    decoded = json.loads(module.serialize_evidence(source))
    assert decoded["observations"][0]["returncode"] is None
    assert decoded["observations"][0]["reason_code"] == "COMMAND_MISSING"
    assert decoded["approval_eligibility"] == "blocked"


def test_exact_input_cardinality_and_stream_size_are_accepted(module):
    source = report(observations=[observation() for _ in range(128)])
    assert len(json.loads(module.serialize_evidence(source))["observations"]) == 128
    source = report(observations=[observation(stdout=b"x" * 32768, stderr=b"")])
    saved = json.loads(module.serialize_evidence(source))["observations"][0]
    assert len(base64.b64decode(saved["stdout_base64"])) == 32768


def test_unsupported_keys_are_rejected_without_hash_or_string_callbacks(module):
    calls = []

    class Key:
        def __hash__(self):
            calls.append("hash")
            return 1

        def __str__(self):
            calls.append("str")
            raise AssertionError

    source = report(metadata={Key(): "value"})
    calls.clear()
    with pytest.raises(module.EvidenceError):
        module.serialize_evidence(source)
    assert calls == []
