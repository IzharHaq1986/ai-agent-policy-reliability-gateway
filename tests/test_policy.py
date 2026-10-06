"""Acceptance tests for the synthetic policy boundary."""

import builtins
import os
import socket
import sqlite3
import subprocess
from copy import deepcopy

import pytest

from gateway.policy import evaluate_request


def request(
    item_id: object = 1,
    tool: object = "fixture_store",
    operation: object = "READ_ITEM",
) -> dict[str, object]:
    return {
        "tool": tool,
        "operation": operation,
        "arguments": {"item_id": item_id},
    }


def assert_result(value: dict[str, object], decision: str, reason: str) -> None:
    assert value == {
        "decision": decision,
        "reason_code": reason,
        "policy_version": "policy-core-v1",
        "execution_status": "not_executed",
    }


@pytest.mark.parametrize("item_id", [1, 2, 999, 1000])
def test_permitted_boundaries(item_id: int) -> None:
    assert_result(
        evaluate_request(request(item_id), principal_id="reliability-reader"),
        "ALLOW",
        "POLICY_ALLOW",
    )


@pytest.mark.parametrize(
    "principal",
    [None, "", 1, True, [], {}, " reader", "reader ", "é", "a" * 65],
)
def test_invalid_identity_takes_precedence(principal: object) -> None:
    assert_result(
        evaluate_request(None, principal_id=principal),
        "DENY",
        "UNAUTHENTICATED",
    )


@pytest.mark.parametrize(
    "item_id",
    [0, -1, 1001, True, False, 1.0, "1", None, [], {}, 10**100],
)
def test_invalid_item_ids(item_id: object) -> None:
    assert_result(
        evaluate_request(request(item_id), principal_id="reliability-reader"),
        "DENY",
        "INVALID_REQUEST",
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "request",
        {},
        {"tool": "fixture_store"},
        {**request(), "principal_id": "reliability-reader"},
        {**request(), "arguments": {}},
        {**request(), "arguments": {"item_id": 1, "extra": True}},
        {**request(), "arguments": []},
        request(tool=""),
        request(tool="a" * 65),
        request(tool="fixture_store\n"),
        request(tool="é"),
        request(tool=1),
        request(operation=""),
        request(operation="READ ITEM"),
        request(operation=True),
    ],
)
def test_malformed_requests(value: object) -> None:
    assert_result(
        evaluate_request(value, principal_id="reliability-reader"),
        "DENY",
        "INVALID_REQUEST",
    )


@pytest.mark.parametrize(
    ("value", "principal", "reason"),
    [
        (request(tool="unknown"), "reliability-reader", "UNKNOWN_TOOL"),
        (request(tool="FIXTURE_STORE"), "reliability-reader", "UNKNOWN_TOOL"),
        (request(operation="WRITE_ITEM"), "reliability-reader", "UNKNOWN_OPERATION"),
        (request(operation="read_item"), "reliability-reader", "UNKNOWN_OPERATION"),
        (request(), "other-reader", "NOT_AUTHORIZED"),
        (request(), "Reliability-reader", "NOT_AUTHORIZED"),
        (request(tool="unknown", operation="unknown"), "other", "UNKNOWN_TOOL"),
        (request(operation="unknown"), "other", "UNKNOWN_OPERATION"),
        (request(item_id=0, tool="unknown"), "other", "INVALID_REQUEST"),
    ],
)
def test_policy_denials_and_precedence(
    value: object, principal: object, reason: str
) -> None:
    assert_result(evaluate_request(value, principal_id=principal), "DENY", reason)


def test_request_claim_cannot_override_trusted_identity() -> None:
    spoofed = {**request(), "principal_id": "reliability-reader"}
    assert_result(
        evaluate_request(spoofed, principal_id="other"),
        "DENY",
        "INVALID_REQUEST",
    )
    assert_result(
        evaluate_request(request(), principal_id="other"),
        "DENY",
        "NOT_AUTHORIZED",
    )


@pytest.mark.parametrize("principal", ["reliability-reader", "other"])
def test_determinism_and_input_preservation(principal: str) -> None:
    value = request()
    original = deepcopy(value)
    first = evaluate_request(value, principal_id=principal)
    second = evaluate_request(value, principal_id=principal)
    assert first == second
    assert first is not second
    assert value == original


def test_builtin_subclasses_are_rejected() -> None:
    class CustomInt(int):
        pass

    class CustomStr(str):
        pass

    class CustomDict(dict[str, object]):
        pass

    for value in (
        request(item_id=CustomInt(1)),
        request(tool=CustomStr("fixture_store")),
        CustomDict(request()),
        {**request(), "arguments": CustomDict(item_id=1)},
    ):
        assert_result(
            evaluate_request(value, principal_id="reliability-reader"),
            "DENY",
            "INVALID_REQUEST",
        )

    assert_result(
        evaluate_request(request(), principal_id=CustomStr("reliability-reader")),
        "DENY",
        "UNAUTHENTICATED",
    )


def test_decisions_do_not_call_operational_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def prohibited(*args: object, **kwargs: object) -> None:
        raise AssertionError("Operational interface invoked")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", prohibited)
        patch.setattr(os, "open", prohibited)
        patch.setattr(socket, "socket", prohibited)
        patch.setattr(subprocess, "run", prohibited)
        patch.setattr(subprocess, "Popen", prohibited)
        patch.setattr(sqlite3, "connect", prohibited)

        for principal, reason, decision in (
            ("reliability-reader", "POLICY_ALLOW", "ALLOW"),
            ("other", "NOT_AUTHORIZED", "DENY"),
        ):
            assert_result(
                evaluate_request(request(), principal_id=principal),
                decision,
                reason,
            )
