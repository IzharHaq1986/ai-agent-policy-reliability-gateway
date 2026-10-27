"""Contract checks for pure synthetic-result provenance metadata."""

from copy import deepcopy  # Compare inputs before and after validation.
from hashlib import sha256  # Independently fingerprint explicit expected bytes.

import pytest  # Supply boundary cases and expected rejections.

from gateway.fixture_provenance import (  # Exercise the public component interface.
    FixtureProvenanceValidationError,
    build_fixture_provenance,
    validate_fixture_provenance,
)

STAMP = "2026-10-27T12:00:00Z"  # Supply a fixed time without reading the clock.


class IntegerSubclass(int):  # Exercise rejection of integer subclasses.
    pass


class StringSubclass(str):  # Exercise rejection of string subclasses.
    pass


class DictionarySubclass(dict):  # Exercise rejection of dictionary subclasses.
    pass


def answer(item_id=42):  # Return an independently constructed synthetic answer.
    return {"item_id": item_id, "label": f"Synthetic item {item_id}"}


def record(item_id=42):  # Build expected metadata without the production builder.
    encoded = (
        f'{{"item_id":{item_id},"label":"Synthetic item {item_id}"}}'
    ).encode()  # Specify the exact expected canonical bytes independently.
    return {
        "schema_version": 1,  # Select the supported format.
        "source_type": "synthetic_fixture",  # Identify the synthetic category.
        "source_id": f"fixture_store:READ_ITEM:{item_id}",  # Bind the item.
        "source_version": "synthetic-fixture-v1",  # Use the supported source label.
        "observed_at": STAMP,  # Supply an explicit observation claim.
        "result_sha256": sha256(encoded).hexdigest(),  # Fingerprint expected bytes.
        "permitted_use": "synthetic_demonstration_only",  # Describe intended use.
    }


def assert_rejected(candidate, *, result=None, expected_item_id=42):
    supplied = answer() if result is None else result  # Supply an independent answer.
    original = deepcopy(candidate)  # Preserve metadata for a mutation check.
    original_answer = deepcopy(supplied)  # Preserve answer contents too.
    with pytest.raises(
        FixtureProvenanceValidationError, match="^Invalid fixture provenance$"
    ) as caught:  # Require the generic component error.
        validate_fixture_provenance(
            candidate, result=supplied, expected_item_id=expected_item_id
        )
    assert caught.value.__suppress_context__ or caught.value.__context__ is None
    assert candidate == original  # Rejection must preserve metadata.
    assert supplied == original_answer  # Rejection must preserve the answer.


@pytest.mark.parametrize("item_id", [1, 2, 42, 500, 999, 1000])
def test_exact_metadata_and_fresh_copy(item_id):  # Check boundaries and exact encoding.
    supplied = answer(item_id)  # Construct the expected fixture answer.
    original = deepcopy(supplied)  # Preserve input for comparison.
    expected = record(item_id)  # Calculate an independent expected record.
    built = build_fixture_provenance(
        supplied, expected_item_id=item_id, observed_at=STAMP
    )
    assert built == expected  # Verify every field and the exact digest.
    checked = validate_fixture_provenance(
        expected, result=supplied, expected_item_id=item_id
    )
    assert checked == expected and checked is not expected  # Return a fresh record.
    checked["source_id"] = "changed"  # Mutate only the returned dictionary.
    built["observed_at"] = "changed"  # Check independent builder output too.
    assert expected == record(item_id)  # Preserve supplied metadata.
    assert supplied == original  # Preserve the original answer.


def test_dictionary_order_does_not_change_digest():  # Ignore input insertion order.
    first = answer()  # Insert item_id before label.
    second = {"label": "Synthetic item 42", "item_id": 42}  # Reverse insertion order.
    assert build_fixture_provenance(
        first, expected_item_id=42, observed_at=STAMP
    ) == build_fixture_provenance(second, expected_item_id=42, observed_at=STAMP)


@pytest.mark.parametrize(
    "stamp",
    ["0001-01-01T00:00:00Z", "2024-02-29T23:59:59Z", "9999-12-31T23:59:59Z"],
)
def test_calendar_boundaries_are_accepted(stamp):  # Check valid extremes and leap day.
    expected = record()  # Start from independent valid metadata.
    expected["observed_at"] = stamp  # Supply the selected valid timestamp.
    assert (
        build_fixture_provenance(answer(), expected_item_id=42, observed_at=stamp)
        == expected
    )
    assert (
        validate_fixture_provenance(expected, result=answer(), expected_item_id=42)
        == expected
    )


@pytest.mark.parametrize(
    "stamp",
    [
        "",
        "2026-02-29T00:00:00Z",
        "2026-04-31T00:00:00Z",
        "0000-01-01T00:00:00Z",
        "10000-01-01T00:00:00Z",
        "2026-00-01T00:00:00Z",
        "2026-13-01T00:00:00Z",
        "2026-10-00T00:00:00Z",
        "2026-10-27T24:00:00Z",
        "2026-10-27T12:60:00Z",
        "2026-10-27T12:00:60Z",
        "2026-10-27T12:00:00+00:00",
        "2026-10-27T12:00:00.000Z",
        "2026-10-27t12:00:00Z",
        "2026-10-27T12:00:00z",
        "2026-10-27T12:00:00Z\n",
        " 2026-10-27T12:00:00Z",
        "２０２６-10-27T12:00:00Z",
    ],
)
def test_invalid_timestamps_are_rejected(stamp):  # Require exact UTC calendar text.
    candidate = record()  # Begin with valid metadata.
    candidate["observed_at"] = stamp  # Introduce one timestamp violation.
    assert_rejected(candidate)  # Check direct validation and input preservation.
    with pytest.raises(
        FixtureProvenanceValidationError, match="^Invalid fixture provenance$"
    ):
        build_fixture_provenance(answer(), expected_item_id=42, observed_at=stamp)


@pytest.mark.parametrize("field", tuple(record()))
def test_missing_fields_are_rejected(field):  # Require the complete record.
    candidate = record()  # Start from valid metadata.
    del candidate[field]  # Remove exactly one required field.
    assert_rejected(candidate)


@pytest.mark.parametrize("field", ["credentials", "result", "metadata"])
def test_extra_fields_are_rejected(field):  # Exclude undeclared contents.
    candidate = record()  # Start from valid metadata.
    candidate[field] = "private"  # Add unsupported content.
    assert_rejected(candidate)


@pytest.mark.parametrize(
    "candidate", [None, [], True, 42, "record", DictionarySubclass(record())]
)
def test_invalid_containers_are_rejected(candidate):  # Require a built-in dictionary.
    assert_rejected(candidate)


@pytest.mark.parametrize("value", [True, False, 0, 2, 1.0, "1", IntegerSubclass(1)])
def test_invalid_versions_are_rejected(value):  # Require the actual integer 1.
    candidate = record()  # Start from valid metadata.
    candidate["schema_version"] = value  # Change only the version.
    assert_rejected(candidate)


@pytest.mark.parametrize("field", [key for key in record() if key != "schema_version"])
@pytest.mark.parametrize("value", [None, True, 42, [], {}])
def test_text_fields_require_builtin_strings(field, value):  # Reject other types.
    candidate = record()  # Start from valid metadata.
    candidate[field] = value  # Introduce one text-type error.
    assert_rejected(candidate)


@pytest.mark.parametrize("field", [key for key in record() if key != "schema_version"])
def test_string_subclasses_are_rejected(field):  # Reject even equal subclass values.
    candidate = record()  # Start from valid metadata.
    candidate[field] = StringSubclass(
        candidate[field]
    )  # Preserve text but change type.
    assert_rejected(candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_type", "real_system"),
        ("source_id", "fixture_store:READ_ITEM:43"),
        ("source_id", "fixture_store:READ_ITEM:042"),
        ("source_version", "synthetic-fixture-v2"),
        ("permitted_use", "operational_control"),
        ("result_sha256", "0" * 64),
        ("result_sha256", record()["result_sha256"].upper()),
        ("result_sha256", record(43)["result_sha256"]),
    ],
)
def test_wrong_claims_or_fingerprints_are_rejected(field, value):
    candidate = record()  # Start from valid metadata.
    candidate[field] = value  # Change a claimed source or content relationship.
    assert_rejected(candidate)


@pytest.mark.parametrize(
    "expected", [True, None, "42", 42.0, IntegerSubclass(42), 0, 1001]
)
def test_invalid_expected_item_is_rejected(expected):  # Reuse strict fixture checking.
    assert_rejected(record(), expected_item_id=expected)
    with pytest.raises(
        FixtureProvenanceValidationError, match="^Invalid fixture provenance$"
    ):
        build_fixture_provenance(answer(), expected_item_id=expected, observed_at=STAMP)


@pytest.mark.parametrize(
    "supplied",
    [
        answer(43),
        {"item_id": True, "label": "Synthetic item 42"},
        {"item_id": 42, "label": "wrong"},
        {"item_id": 42},
        {**answer(), "extra": "private"},
        DictionarySubclass(answer()),
    ],
)
def test_invalid_answer_is_rejected(supplied):  # Reject content before fingerprinting.
    assert_rejected(record(), result=supplied)
    with pytest.raises(
        FixtureProvenanceValidationError, match="^Invalid fixture provenance$"
    ):
        build_fixture_provenance(supplied, expected_item_id=42, observed_at=STAMP)


def test_other_item_cannot_reuse_a_valid_record():  # Bind source and digest to context.
    assert_rejected(record(), result=answer(43), expected_item_id=43)


@pytest.mark.parametrize("stamp", [None, True, 42, StringSubclass(STAMP)])
def test_builder_rejects_non_builtin_timestamp_text(stamp):  # Validate builder inputs.
    with pytest.raises(
        FixtureProvenanceValidationError, match="^Invalid fixture provenance$"
    ):
        build_fixture_provenance(answer(), expected_item_id=42, observed_at=stamp)


def test_missing_builder_argument_retains_type_error():  # Preserve Python call rules.
    with pytest.raises(TypeError):
        build_fixture_provenance(answer(), expected_item_id=42)
