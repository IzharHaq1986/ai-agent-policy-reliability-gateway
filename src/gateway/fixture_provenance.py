"""Pure synthetic-result metadata; no authenticity or execution established."""

import json  # Encode validated answers in a deterministic form.
import re  # Require the exact supported timestamp shape.
from datetime import datetime  # Check supplied calendar values without reading time.
from hashlib import sha256  # Calculate the checked answer's content fingerprint.
from typing import Literal, TypedDict, cast  # Describe the validated metadata.

from gateway.fixture_result import (  # Reuse exact synthetic-answer validation.
    FixtureResultValidationError,
    validate_fixture_result,
)


class FixtureProvenanceValidationError(ValueError):  # Identify component failures.
    """An answer or metadata record violates the provenance contract."""


class FixtureProvenance(TypedDict):  # Describe exactly the supported metadata.
    schema_version: Literal[1]  # Identify the record format.
    source_type: Literal["synthetic_fixture"]  # Label the data as synthetic.
    source_id: str  # Identify the claimed fixture tool, operation, and item.
    source_version: Literal[
        "synthetic-fixture-v1"
    ]  # Label the supported source format.
    observed_at: str  # Retain the supplied observation-time claim.
    result_sha256: str  # Bind metadata to the validated answer's encoded contents.
    permitted_use: Literal["synthetic_demonstration_only"]  # Describe intended use.


_KEYS = frozenset(FixtureProvenance.__annotations__)  # Require the exact field set.
_TEXT_KEYS = _KEYS - {"schema_version"}  # Require ordinary strings for other fields.
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)  # Require whole seconds, ASCII digits, and the explicit UTC suffix.


def _result_digest(result: object, expected_item_id: object) -> str:
    """Hash a fresh validated answer using the contract's fixed encoding."""
    try:  # Normalize invalid answers and expected identifiers at this boundary.
        checked = validate_fixture_result(
            result, expected_item_id=expected_item_id
        )  # Validate before serialization or hashing.
    except FixtureResultValidationError:  # Keep supplied data out of error output.
        raise FixtureProvenanceValidationError("Invalid fixture provenance") from None
    encoded = json.dumps(
        checked,
        sort_keys=True,  # Make dictionary insertion order irrelevant.
        separators=(",", ":"),  # Exclude optional whitespace.
        ensure_ascii=True,  # Fix the string-escaping convention.
        allow_nan=False,  # Reject non-finite numbers if the answer contract changes.
    ).encode("utf-8")  # Hash the specified byte representation.
    return sha256(encoded).hexdigest()  # Return lowercase hexadecimal text.


def validate_fixture_provenance(
    provenance: object,  # Receive untrusted metadata.
    *,
    result: object,  # Receive the answer to which the metadata must refer.
    expected_item_id: object,  # Require separately supplied expected context.
) -> FixtureProvenance:
    """Return fresh checked metadata without proving the supplied claims."""
    digest = _result_digest(
        result, expected_item_id
    )  # Recompute from the exact answer.
    if type(provenance) is not dict or provenance.keys() != _KEYS:
        raise FixtureProvenanceValidationError("Invalid fixture provenance")
    if type(provenance["schema_version"]) is not int:
        raise FixtureProvenanceValidationError("Invalid fixture provenance")
    if provenance["schema_version"] != 1:  # Accept only the supported format.
        raise FixtureProvenanceValidationError("Invalid fixture provenance")
    if any(type(provenance[key]) is not str for key in _TEXT_KEYS):
        raise FixtureProvenanceValidationError("Invalid fixture provenance")

    if (
        provenance["source_type"]
        != "synthetic_fixture"  # Restrict the source category.
        or provenance["source_id"] != f"fixture_store:READ_ITEM:{expected_item_id}"
        or provenance["source_version"] != "synthetic-fixture-v1"
        or provenance["permitted_use"] != "synthetic_demonstration_only"
        or provenance["result_sha256"] != digest  # Require exact content agreement.
    ):
        raise FixtureProvenanceValidationError("Invalid fixture provenance")

    stamp = provenance["observed_at"]  # Check the supplied claim, not current time.
    if _TIMESTAMP.fullmatch(stamp) is None:  # Reject alternate timestamp formats.
        raise FixtureProvenanceValidationError("Invalid fixture provenance")
    try:  # Check real calendar and time ranges after the shape check.
        datetime.fromisoformat(stamp)  # Reject year zero, impossible dates, and times.
    except ValueError:  # Suppress parser details containing supplied data.
        raise FixtureProvenanceValidationError("Invalid fixture provenance") from None

    return cast(FixtureProvenance, dict(provenance))  # Copy checked immutable values.


def build_fixture_provenance(
    result: object,  # Receive the synthetic answer.
    *,
    expected_item_id: object,  # Receive the expected item independently.
    observed_at: object,  # Receive a claimed observation time without generating it.
) -> FixtureProvenance:
    """Build checked metadata without invocation, storage, or clock access."""
    digest = _result_digest(result, expected_item_id)  # Validate and fingerprint input.
    return validate_fixture_provenance(
        {
            "schema_version": 1,  # Supply the supported record format.
            "source_type": "synthetic_fixture",  # Clearly identify synthetic data.
            "source_id": f"fixture_store:READ_ITEM:{expected_item_id}",
            "source_version": "synthetic-fixture-v1",  # Supply the supported label.
            "observed_at": observed_at,  # Preserve the supplied time for validation.
            "result_sha256": digest,  # Bind the record to the checked answer.
            "permitted_use": "synthetic_demonstration_only",  # Describe intended use.
        },
        result=result,  # Apply the same complete validation as direct callers.
        expected_item_id=expected_item_id,  # Preserve the independently expected item.
    )
