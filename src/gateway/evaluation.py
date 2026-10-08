"""Deterministic evaluation of synthetic policy scenarios without I/O."""

import re
from typing import TypedDict

from gateway.audit import AuditValidationError, validate_policy_result
from gateway.policy import PolicyResult, evaluate_request

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}")
_CATEGORIES = frozenset(
    {
        "permitted_request",
        "unauthorized_caller",
        "unsupported_tool",
        "unsupported_operation",
        "malformed_request",
        "untrusted_instruction",
    }
)
_SCENARIO_KEYS = frozenset(
    {
        "scenario_id",
        "category",
        "request",
        "principal_id",
        "expected_result",
    }
)


class EvaluationDataError(ValueError):
    """The supplied dataset does not satisfy the evaluation contract."""


class _Scenario(TypedDict):
    scenario_id: str
    category: str
    request: object
    principal_id: object
    expected_result: PolicyResult


class EvaluationCounts(TypedDict):
    total: int
    passed: int
    failed: int


class ScenarioOutcome(TypedDict):
    scenario_id: str
    category: str
    passed: bool
    mismatch_fields: list[str]


class EvaluationReport(EvaluationCounts):
    pass_rate: float
    categories: dict[str, EvaluationCounts]
    scenarios: list[ScenarioOutcome]


def _validate_dataset(dataset: object) -> list[_Scenario]:
    if type(dataset) is not dict or dataset.keys() != {"schema_version", "scenarios"}:
        raise EvaluationDataError("Invalid dataset fields")
    if type(dataset["schema_version"]) is not int or dataset["schema_version"] != 1:
        raise EvaluationDataError("Unsupported dataset schema version")
    entries = dataset["scenarios"]
    if type(entries) is not list or not 1 <= len(entries) <= 100:
        raise EvaluationDataError("Invalid scenario collection")

    validated: list[_Scenario] = []
    identifiers: set[str] = set()
    for entry in entries:
        if type(entry) is not dict or entry.keys() != _SCENARIO_KEYS:
            raise EvaluationDataError("Invalid scenario fields")
        identifier = entry["scenario_id"]
        category = entry["category"]
        if type(identifier) is not str or _IDENTIFIER.fullmatch(identifier) is None:
            raise EvaluationDataError("Invalid scenario identifier")
        if identifier in identifiers:
            raise EvaluationDataError("Duplicate scenario identifier")
        if type(category) is not str or category not in _CATEGORIES:
            raise EvaluationDataError("Unsupported scenario category")
        try:
            expected = validate_policy_result(entry["expected_result"])
        except AuditValidationError:
            raise EvaluationDataError("Invalid expected policy result") from None

        identifiers.add(identifier)
        validated.append(
            {
                "scenario_id": identifier,
                "category": category,
                "request": entry["request"],
                "principal_id": entry["principal_id"],
                "expected_result": expected.copy(),
            }
        )
    return validated


def _mismatch_fields(actual: object, expected: PolicyResult) -> list[str]:
    if type(actual) is not dict:
        return ["result_type"]
    mismatches = []
    if actual.keys() != expected.keys():
        mismatches.append("result_fields")
    expected_values: dict[str, object] = dict(expected)
    for field in sorted(expected_values):
        if (
            field not in actual
            or type(actual[field]) is not str
            or actual[field] != expected_values[field]
        ):
            mismatches.append(field)
    return mismatches


def evaluate_dataset(dataset: object) -> EvaluationReport:
    """Evaluate a fully validated dataset once in order without modifying it.

    Metrics measure synthetic policy correctness, not model or release safety.
    Unexpected evaluator exceptions propagate.
    """
    scenarios = _validate_dataset(dataset)
    categories: dict[str, EvaluationCounts] = {
        category: {"total": 0, "passed": 0, "failed": 0}
        for category in sorted({scenario["category"] for scenario in scenarios})
    }
    outcomes: list[ScenarioOutcome] = []
    passed = 0
    for scenario in scenarios:
        actual = evaluate_request(
            scenario["request"], principal_id=scenario["principal_id"]
        )
        mismatches = _mismatch_fields(actual, scenario["expected_result"])
        success = not mismatches
        passed += int(success)
        counts = categories[scenario["category"]]
        counts["total"] += 1
        if success:
            counts["passed"] += 1
        else:
            counts["failed"] += 1
        outcomes.append(
            {
                "scenario_id": scenario["scenario_id"],
                "category": scenario["category"],
                "passed": success,
                "mismatch_fields": mismatches,
            }
        )

    total = len(scenarios)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total,
        "categories": categories,
        "scenarios": outcomes,
    }
