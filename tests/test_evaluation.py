"""Acceptance checks for synthetic policy evaluation and its dataset gate."""

import builtins
import json
import os
import socket
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from gateway import evaluation
from gateway.evaluation import EvaluationDataError, evaluate_dataset

DATASET = Path(__file__).resolve().parents[1] / "evaluations/policy_core_v1.json"


def dataset():
    return json.loads(DATASET.read_text())


def test_committed_dataset_quality_gate():
    supplied = dataset()
    original = deepcopy(supplied)
    report = evaluate_dataset(supplied)
    assert report["total"] == 12
    assert report["passed"] == 12
    assert report["failed"] == 0
    assert report["pass_rate"] == 1.0
    assert len(report["categories"]) == 6
    assert all(
        counts == {"total": 2, "passed": 2, "failed": 0}
        for counts in report["categories"].values()
    )
    assert all(outcome["mismatch_fields"] == [] for outcome in report["scenarios"])
    assert supplied == original
    assert evaluate_dataset(supplied) == report


def test_evaluates_each_scenario_once_in_order(monkeypatch):
    supplied = dataset()
    real_evaluator = evaluation.evaluate_request
    calls = []

    def record(request, *, principal_id):
        calls.append((request, principal_id))
        return real_evaluator(request, principal_id=principal_id)

    monkeypatch.setattr(evaluation, "evaluate_request", record)
    report = evaluate_dataset(supplied)
    assert calls == [
        (case["request"], case["principal_id"]) for case in supplied["scenarios"]
    ]
    assert [case["scenario_id"] for case in report["scenarios"]] == [
        case["scenario_id"] for case in supplied["scenarios"]
    ]


def test_mismatch_metrics_are_not_hidden():
    supplied = dataset()
    supplied["scenarios"][0]["expected_result"].update(
        decision="DENY", reason_code="NOT_AUTHORIZED"
    )
    original = deepcopy(supplied)
    report = evaluate_dataset(supplied)
    assert report["total"] == 12
    assert report["passed"] == 11
    assert report["failed"] == 1
    assert report["pass_rate"] == 11 / 12
    assert report["categories"]["permitted_request"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
    }
    assert report["scenarios"][0]["mismatch_fields"] == ["decision", "reason_code"]
    assert sum(value["total"] for value in report["categories"].values()) == 12
    assert sum(value["failed"] for value in report["categories"].values()) == 1
    assert supplied == original


def rejected_without_evaluation(monkeypatch, supplied):
    calls = []

    def prohibited(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Invalid dataset reached evaluator")

    monkeypatch.setattr(evaluation, "evaluate_request", prohibited)
    original = deepcopy(supplied)
    with pytest.raises(EvaluationDataError):
        evaluate_dataset(supplied)
    assert calls == []
    assert supplied == original


@pytest.mark.parametrize("supplied", [None, [], "", 1, True, {}])
def test_invalid_top_level(monkeypatch, supplied):
    rejected_without_evaluation(monkeypatch, supplied)


@pytest.mark.parametrize("version", [True, "1", 0, 2, None])
def test_invalid_schema_version(monkeypatch, version):
    supplied = dataset()
    supplied["schema_version"] = version
    rejected_without_evaluation(monkeypatch, supplied)


@pytest.mark.parametrize("entries", [None, [], (), {}, "cases", [None]])
def test_invalid_scenario_collection(monkeypatch, entries):
    supplied = dataset()
    supplied["scenarios"] = entries
    rejected_without_evaluation(monkeypatch, supplied)


def test_excess_scenarios(monkeypatch):
    supplied = dataset()
    supplied["scenarios"] = [supplied["scenarios"][0]] * 101
    rejected_without_evaluation(monkeypatch, supplied)


@pytest.mark.parametrize(
    "field", ["scenario_id", "category", "request", "principal_id", "expected_result"]
)
def test_missing_scenario_field(monkeypatch, field):
    supplied = dataset()
    del supplied["scenarios"][-1][field]
    rejected_without_evaluation(monkeypatch, supplied)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario_id", ""),
        ("scenario_id", "é"),
        ("scenario_id", "a" * 65),
        ("scenario_id", 1),
        ("category", "unknown"),
        ("category", True),
        ("extra", "untrusted"),
    ],
)
def test_invalid_scenario_metadata(monkeypatch, field, value):
    supplied = dataset()
    supplied["scenarios"][-1][field] = value
    rejected_without_evaluation(monkeypatch, supplied)


def test_duplicate_identifier(monkeypatch):
    supplied = dataset()
    supplied["scenarios"][-1]["scenario_id"] = supplied["scenarios"][0]["scenario_id"]
    rejected_without_evaluation(monkeypatch, supplied)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "UNKNOWN"),
        ("decision", True),
        ("reason_code", "UNKNOWN"),
        ("decision", "DENY"),
        ("policy_version", "other"),
        ("execution_status", "executed"),
        ("extra", "untrusted"),
    ],
)
def test_invalid_expected_result(monkeypatch, field, value):
    supplied = dataset()
    supplied["scenarios"][-1]["expected_result"][field] = value
    if field == "decision" and value == "DENY":
        supplied["scenarios"][0]["expected_result"]["decision"] = value
    rejected_without_evaluation(monkeypatch, supplied)


@pytest.mark.parametrize(
    ("actual", "mismatches"),
    [
        (None, ["result_type"]),
        (
            {},
            [
                "result_fields",
                "decision",
                "execution_status",
                "policy_version",
                "reason_code",
            ],
        ),
        (
            {
                "decision": "ALLOW",
                "reason_code": "POLICY_ALLOW",
                "policy_version": "other",
                "execution_status": "not_executed",
            },
            ["policy_version"],
        ),
        (
            {
                "decision": "ALLOW",
                "reason_code": "POLICY_ALLOW",
                "policy_version": "policy-core-v1",
                "execution_status": "executed",
            },
            ["execution_status"],
        ),
        (
            {
                "decision": "ALLOW",
                "reason_code": "POLICY_ALLOW",
                "policy_version": "policy-core-v1",
                "execution_status": "not_executed",
                "extra": "unexpected",
            },
            ["result_fields"],
        ),
    ],
)
def test_complete_actual_result_comparison(monkeypatch, actual, mismatches):
    supplied = dataset()
    supplied["scenarios"] = supplied["scenarios"][:1]
    monkeypatch.setattr(evaluation, "evaluate_request", lambda *args, **kwargs: actual)
    report = evaluate_dataset(supplied)
    assert report["failed"] == 1
    assert report["scenarios"][0]["mismatch_fields"] == mismatches


def test_unexpected_evaluator_exception_propagates(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic evaluator failure")

    monkeypatch.setattr(evaluation, "evaluate_request", fail)
    with pytest.raises(RuntimeError, match="Synthetic evaluator failure"):
        evaluate_dataset(dataset())


def test_runner_invokes_no_io_interfaces(monkeypatch):
    supplied = dataset()

    def prohibited(*args, **kwargs):
        raise AssertionError("Evaluation invoked an I/O interface")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", prohibited)
        patch.setattr(os, "open", prohibited)
        patch.setattr(socket, "socket", prohibited)
        patch.setattr(subprocess, "run", prohibited)
        patch.setattr(subprocess, "Popen", prohibited)
        assert evaluate_dataset(supplied)["failed"] == 0
