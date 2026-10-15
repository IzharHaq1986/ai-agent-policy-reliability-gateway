"""Transactional PostgreSQL delivery of a policy event and request binding."""

from collections.abc import Callable  # Describe the returned synchronous receiver.
from contextlib import suppress  # Keep cleanup failures from hiding the original error.

import psycopg  # Deliver both records through one PostgreSQL connection.

from gateway.audit import (
    AuditValidationError,
    validate_audit_event,
)  # Check policy data.
from gateway.request_binding import (  # Reuse the existing binding contract.
    RequestBindingValidationError,  # Identify invalid binding inputs.
    validate_request_binding,  # Return an independent checked binding.
)  # Finish the binding imports.

_POLICY_INSERT = """
INSERT INTO gateway_audit.policy_decisions (
    schema_version, event_type, event_id, occurred_at, principal_id,
    decision, reason_code, policy_version, execution_status
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""  # Insert a new policy event; existing identifiers must fail.

_BINDING_INSERT = """
INSERT INTO gateway_audit.request_bindings (
    schema_version, policy_event_id, principal_id, tool, operation, item_id
)
VALUES (%s, %s, %s, %s, %s, %s)
"""  # Insert its binding using parameters, never interpolated record values.


class BindingPairValidationError(ValueError):  # Identify rejected record pairs.
    """The supplied records do not satisfy the supported pair contract."""


def create_postgresql_binding_receiver(  # Configure explicit paired persistence.
    conninfo: str,  # Receive database configuration from trusted application setup.
) -> Callable[[object, object], None]:  # Return a receiver for two untrusted records.
    """Create a receiver; failed commit acknowledgement can leave a saved pair."""
    if type(conninfo) is not str or not conninfo.strip():  # Require non-empty text.
        raise ValueError(
            "Invalid binding database configuration"
        )  # Hide configuration.

    def receive(  # Validate each pair before opening a database connection.
        policy_event: object,  # Receive the claimed policy event.
        request_binding: object,  # Receive the request associated with that event.
    ) -> None:  # Report success only after commit and close.
        try:  # Normalize documented validation failures without exposing input.
            policy = validate_audit_event(
                policy_event
            )  # Copy and check the policy event.
            binding = validate_request_binding(
                request_binding
            )  # Copy and check binding.
        except (
            AuditValidationError,
            RequestBindingValidationError,
        ):  # Reject bad data.
            raise BindingPairValidationError("Invalid policy binding pair") from None
        if (  # Require a matching allowed decision before any database access.
            policy["decision"] != "ALLOW"  # DENY cannot support this persistence path.
            or policy["reason_code"] != "POLICY_ALLOW"  # Require the matching reason.
            or policy["event_id"]
            != binding["policy_event_id"]  # Match the decision ID.
            or policy["principal_id"]
            != binding["principal_id"]  # Match caller context.
        ):  # Structural validation alone is insufficient for a pair.
            raise BindingPairValidationError("Invalid policy binding pair")

        policy_values = (  # Match the policy INSERT column order exactly.
            policy["schema_version"],  # Store the event format.
            policy["event_type"],  # Store the policy-event category.
            policy["event_id"],  # Store the decision identifier.
            policy["occurred_at"],  # Store the supplied occurrence time.
            policy["principal_id"],  # Store the validated caller name.
            policy["decision"],  # Store ALLOW.
            policy["reason_code"],  # Store POLICY_ALLOW.
            policy["policy_version"],  # Store the supported policy version.
            policy["execution_status"],  # Preserve not_executed.
        )  # Keep policy values separate from SQL syntax.
        binding_values = (  # Match the binding INSERT column order exactly.
            binding["schema_version"],  # Store the binding format.
            binding["policy_event_id"],  # Link to the inserted policy event.
            binding["principal_id"],  # Store the matching caller name.
            binding["tool"],  # Store the supported practice tool.
            binding["operation"],  # Store the supported read operation.
            binding["item_id"],  # Store the requested synthetic item.
        )  # Keep binding values separate from SQL syntax.

        connection = psycopg.connect(
            conninfo, connect_timeout=5
        )  # Open one connection.
        try:  # Roll back pre-commit failures and always attempt failure cleanup.
            connection.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
            # Request synchronous commit for this transaction.
            connection.execute("SET LOCAL synchronous_commit = on")
            # Bound each database lock wait.
            connection.execute("SET LOCAL lock_timeout = '2s'")
            # Bound each database statement, not the entire receiver call.
            connection.execute("SET LOCAL statement_timeout = '5s'")
            connection.execute(
                _POLICY_INSERT, policy_values
            )  # Insert the parent first.
            connection.execute(
                _BINDING_INSERT, binding_values
            )  # Insert its linked request.
            connection.commit()  # Commit both inserts together exactly once per call.
        except BaseException:  # Attempt cleanup even when execution is interrupted.
            with suppress(
                Exception
            ):  # Preserve the original failure if rollback fails.
                connection.rollback()  # Cannot undo a commit that already completed.
            with suppress(Exception):  # Still try closing after a rollback failure.
                connection.close()  # Release the connection when possible.
            raise  # Propagate the original failure without retries or fallback.
        else:  # The database acknowledged commit.
            connection.close()  # A close failure propagates despite the saved records.

    return receive  # Creating the receiver itself opens no database connection.
