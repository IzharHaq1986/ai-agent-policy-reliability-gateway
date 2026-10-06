"""Deterministic authorization for a synthetic, non-executing tool."""

import re
from typing import Literal, TypedDict

Decision = Literal["ALLOW", "DENY"]
ReasonCode = Literal[
    "UNAUTHENTICATED",
    "INVALID_REQUEST",
    "UNKNOWN_TOOL",
    "UNKNOWN_OPERATION",
    "NOT_AUTHORIZED",
    "POLICY_ALLOW",
]


class PolicyResult(TypedDict):
    decision: Decision
    reason_code: ReasonCode
    policy_version: Literal["policy-core-v1"]
    execution_status: Literal["not_executed"]


_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _valid_identifier(value: object) -> bool:
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


def _result(decision: Decision, reason_code: ReasonCode) -> PolicyResult:
    return {
        "decision": decision,
        "reason_code": reason_code,
        "policy_version": "policy-core-v1",
        "execution_status": "not_executed",
    }


def evaluate_request(request: object, *, principal_id: object) -> PolicyResult:
    """Evaluate policy; principal identity must come from a trusted caller.

    This function does not authenticate identities or execute tools.
    Inputs are never modified. Invalid inputs produce denial decisions.
    """
    if not _valid_identifier(principal_id):
        return _result("DENY", "UNAUTHENTICATED")

    if type(request) is not dict:
        return _result("DENY", "INVALID_REQUEST")
    if request.keys() != {"tool", "operation", "arguments"}:
        return _result("DENY", "INVALID_REQUEST")

    tool = request["tool"]
    operation = request["operation"]
    arguments = request["arguments"]

    if not _valid_identifier(tool) or not _valid_identifier(operation):
        return _result("DENY", "INVALID_REQUEST")
    if type(arguments) is not dict or arguments.keys() != {"item_id"}:
        return _result("DENY", "INVALID_REQUEST")

    item_id = arguments["item_id"]
    if type(item_id) is not int or not 1 <= item_id <= 1000:
        return _result("DENY", "INVALID_REQUEST")

    if tool != "fixture_store":
        return _result("DENY", "UNKNOWN_TOOL")
    if operation != "READ_ITEM":
        return _result("DENY", "UNKNOWN_OPERATION")
    if principal_id != "reliability-reader":
        return _result("DENY", "NOT_AUTHORIZED")

    return _result("ALLOW", "POLICY_ALLOW")
