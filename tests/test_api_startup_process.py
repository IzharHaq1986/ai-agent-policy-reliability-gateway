"""Real local HTTP checks for the PostgreSQL-audited Uvicorn factory."""

import json  # Encode requests and decode HTTP responses.
import os  # Supply a controlled child-process environment.
import secrets  # Generate independent synthetic API credentials.
import socket  # Reserve a loopback address without a port-selection race.
import subprocess  # Start and reap only the test-owned server.
import sys  # Use the same installed Python interpreter as pytest.
import tempfile  # Keep server output private and temporary.
import time  # Bound startup polling using a monotonic clock.
from contextlib import contextmanager  # Guarantee server cleanup after each case.
from http.client import HTTPConnection  # Send real HTTP without proxy configuration.
from pathlib import Path  # Locate the repository source directory.

import psycopg  # Inspect the explicitly configured isolated database.
import pytest  # Supply fixtures, cases, and bounded test failures.
from psycopg.rows import dict_row  # Read audit records by column name.

from gateway.audit import validate_audit_event  # Check saved event contents.

ROOT = (
    Path(__file__).resolve().parents[1]
)  # Resolve the repository independently of cwd.


@pytest.fixture
def database():  # Require explicit isolated database credentials.
    writer = os.environ.get("GATEWAY_TEST_WRITER_DSN")  # Read test-only writer setup.
    reader = os.environ.get("GATEWAY_TEST_READER_DSN")  # Read test-only reader setup.
    if not writer or not reader:  # Avoid using an implicit operational database.
        pytest.skip("Explicit isolated PostgreSQL test connections required")
    for conninfo, role in (
        (writer, "gateway_audit_writer"),  # Verify the intended writer identity.
        (reader, "gateway_audit_reader"),  # Verify the intended reader identity.
    ):
        with psycopg.connect(conninfo, connect_timeout=5) as connection:
            identity = connection.execute(
                "SELECT current_database(), current_user"
            ).fetchone()  # Inspect actual database identity before test activity.
            assert identity == ("gateway_audit_test", role)  # Reject other targets.
    return writer, reader  # Expose only verified isolated-test connections.


def stored_events(reader):  # Capture evidence in the dedicated serial test database.
    with psycopg.connect(
        reader, connect_timeout=5, row_factory=dict_row
    ) as connection:  # Inspect through a separate least-privilege connection.
        connection.execute("SET LOCAL statement_timeout = '5s'")  # Bound inspection.
        rows = connection.execute(
            """
            SELECT schema_version, event_type, event_id, occurred_at,
                   principal_id, decision, reason_code, policy_version,
                   execution_status
            FROM gateway_audit.policy_decisions
            """
        ).fetchall()  # Read all event identities to detect unexpected additions.
    return {row["event_id"]: row for row in rows}  # Preserve contents for comparison.


def request(port, token=None, payload=None, timeout=5):  # Make one bounded HTTP call.
    connection = HTTPConnection("127.0.0.1", port, timeout=timeout)  # Avoid proxies.
    headers = {"Content-Type": "application/json"}  # Use the existing JSON interface.
    if token is not None:  # Permit unauthenticated readiness and rejection checks.
        headers["Authorization"] = f"Bearer {token}"  # Authenticate selected requests.
    try:  # Close the connection on success and failure.
        connection.request(
            "POST",
            "/v1/policy/evaluate",
            body=json.dumps(payload if payload is not None else {}),
            headers=headers,
        )  # Send a real request to the existing route.
        response = connection.getresponse()  # Wait within the socket timeout.
        body = response.read()  # Consume the small response before closing.
        return response.status, json.loads(body)  # Return only the HTTP result.
    finally:  # Release the HTTP socket even if decoding or delivery fails.
        connection.close()


@contextmanager
def running_server(conninfo):  # Own one server process for one test case.
    token = secrets.token_urlsafe(32)  # Create an independent synthetic credential.
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),  # Allow installed runtime helpers.
        "PYTHONDONTWRITEBYTECODE": "1",  # Avoid generated repository artifacts.
        "GATEWAY_API_TOKEN": token,  # Keep the credential out of process arguments.
        "GATEWAY_AUDIT_CONNINFO": conninfo,  # Supply verified test database settings.
    }  # Exclude inherited gateway, Python, proxy, and libpq configuration.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))  # Reserve an operating-system-selected port.
        listener.listen(128)  # Prepare the reserved socket for the child server.
        port = listener.getsockname()[1]  # Discover the reserved local port.
        with tempfile.TemporaryFile() as output:  # Keep server logs private.
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",  # Run the installed real server.
                    "gateway.api_startup:create_postgresql_app",  # Load actual code.
                    "--factory",
                    "--app-dir",
                    str(ROOT / "src"),  # Match startup wiring.
                    "--fd",
                    str(listener.fileno()),  # Reuse the reserved socket.
                ],
                cwd=ROOT,  # Resolve the application from the intended checkout.
                env=environment,  # Use only the controlled test configuration.
                pass_fds=(listener.fileno(),),  # Share only the reserved socket.
                stdin=subprocess.DEVNULL,  # Prevent interactive input.
                stdout=output,
                stderr=output,  # Never publish raw server error details.
            )
            try:  # Clean up even when readiness or assertions fail.
                deadline = time.monotonic() + 15  # Bound startup observation.
                while True:  # Poll without an authenticated policy evaluation.
                    if process.poll() is not None:  # Detect early startup failure.
                        pytest.fail("Audited API server exited before readiness")
                    remaining = deadline - time.monotonic()  # Track remaining budget.
                    if remaining <= 0:  # Do not wait indefinitely.
                        pytest.fail("Audited API server readiness timed out")
                    try:  # Retry only the unauthenticated readiness probe.
                        status, body = request(port, timeout=min(0.5, remaining))
                    except (OSError, ValueError):  # Allow brief startup unavailability.
                        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                    else:  # Require the expected live authentication boundary.
                        assert (
                            status == 401
                        )  # Readiness must not bypass authentication.
                        assert body == {"error": {"code": "UNAUTHENTICATED"}}
                        break  # The application is serving actual HTTP requests.
                yield port, token  # Let the test exercise the running application.
            finally:  # Reap only the server process created above.
                if process.poll() is None:  # Request shutdown if it is still running.
                    process.terminate()
                try:  # Allow bounded graceful shutdown.
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:  # Escalate only for this child.
                    process.kill()
                    process.wait(timeout=5)  # Ensure the child is reaped.


def payload():  # Build a fresh supported synthetic policy request.
    return {
        "tool": "fixture_store",  # Use the existing bounded tool name.
        "operation": "READ_ITEM",  # Request the supported read operation.
        "arguments": {"item_id": 42},  # Select a valid synthetic item.
    }


@pytest.mark.parametrize(
    ("change", "status", "decision", "reason"),
    [
        ({}, 200, "ALLOW", "POLICY_ALLOW"),  # Exercise normal permission.
        ({"tool": "other_tool"}, 200, "DENY", "UNKNOWN_TOOL"),  # Exercise denial.
        ({"arguments": {"item_id": 0}}, 422, "DENY", "INVALID_REQUEST"),
    ],
)
def test_real_server_commits_decision(database, change, status, decision, reason):
    writer, reader = database  # Require the verified isolated database.
    before = stored_events(reader)  # Capture evidence before server startup.
    with running_server(writer) as (port, token):  # Start the actual audited factory.
        supplied = payload()  # Begin with supported input.
        supplied.update(change)  # Select the policy case.
        actual_status, body = request(port, token, supplied)  # Send exactly once.
        after = stored_events(reader)  # Inspect committed evidence independently.

    assert actual_status == status  # Preserve the public HTTP status.
    assert all(after.get(key) == value for key, value in before.items())
    added = set(after) - set(
        before
    )  # Identify new evidence without timestamp guessing.
    assert len(added) == 1  # Fail on missing or unexpected extra records.
    audit = after[added.pop()]  # Inspect the sole new record in this serial test.
    assert validate_audit_event(audit) == audit  # Require the existing event contract.
    assert audit["principal_id"] == "reliability-reader"  # Preserve trusted identity.
    assert audit["decision"] == decision  # Match the request's expected decision.
    assert audit["reason_code"] == reason  # Match the expected explanation.
    assert audit["execution_status"] == "not_executed"  # Keep HTTP non-executing.
    result = {
        key: audit[key]  # Compare the HTTP decision with independently saved evidence.
        for key in ("decision", "reason_code", "policy_version", "execution_status")
    }
    assert body == (
        result if status == 200 else {"error": {"code": "INVALID_REQUEST"}}
    )  # Preserve invalid-input response semantics too.
    assert token not in json.dumps(audit)  # Keep credentials out of audit content.


def test_real_server_rejects_missing_authentication(database):
    writer, reader = database  # Use the intended audited startup configuration.
    before = stored_events(reader)  # Include startup probes in the no-record check.
    with running_server(writer) as (port, _):  # Start without changing authentication.
        status, body = request(port, payload=payload())  # Omit credentials.
        after = stored_events(reader)  # Inspect through an independent connection.
    assert status == 401  # Require authentication before evaluation.
    assert body == {"error": {"code": "UNAUTHENTICATED"}}  # Preserve safe errors.
    assert after == before  # Neither readiness nor rejected requests create events.


@pytest.mark.parametrize(
    "change", [{}, {"tool": "other_tool"}, {"arguments": {"item_id": 0}}]
)
def test_real_server_requires_database_write_permission(database, change):
    _, reader = database  # Use credentials that can inspect but cannot insert.
    before = stored_events(reader)  # Record the database baseline.
    with running_server(reader) as (port, token):  # Creation does not check permission.
        supplied = payload()  # Build the existing request format.
        supplied.update(change)  # Exercise allowed, denied, and invalid cases.
        status, body = request(port, token, supplied)  # Evaluate with real persistence.
        after = stored_events(reader)  # Check whether any record was saved.
    assert status == 500  # Never return the normal response after audit failure.
    assert body == {"error": {"code": "INTERNAL_ERROR"}}  # Expose no database details.
    assert after == before  # Permission rejection must not create an audit row.


@pytest.mark.parametrize(
    "change",
    [
        {},  # Exercise a request that would normally receive ALLOW.
        {"tool": "other_tool"},  # Exercise an unknown-tool denial.
        {"arguments": {"item_id": 0}},  # Exercise policy-invalid input.
    ],
)
def test_real_server_requires_audit_connection(change):
    # Reserve a private local endpoint without accepting TCP connections.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unavailable:
        unavailable.bind(("127.0.0.1", 0))  # Keep the port reserved throughout.
        database_port = unavailable.getsockname()[1]  # Use the assigned port.
        conninfo = (
            "host=127.0.0.1 hostaddr=127.0.0.1 "
            f"port={database_port} "
            "dbname=synthetic_unavailable_audit user=synthetic_audit_writer "
            "sslmode=disable"
        )  # Use explicit local settings without credentials or service files.

        # Reuse real startup and its unauthenticated HTTP readiness check.
        with running_server(conninfo) as (port, token):
            supplied = payload()  # Begin with an independent supported request.
            supplied.update(change)  # Select the policy case under test.
            status, body = request(
                port, token, supplied, timeout=10
            )  # Send once; bound the HTTP wait beyond the connection timeout.

        # The server is stopped before releasing the reserved failure endpoint.
        assert status == 500  # Withhold every normal policy response.
        assert body == {"error": {"code": "INTERNAL_ERROR"}}  # Hide details.
        encoded = json.dumps(body)  # Inspect only the public response content.
        assert token not in encoded  # Protect the synthetic API credential.
        assert conninfo not in encoded  # Do not expose connection configuration.
        assert "synthetic_unavailable_audit" not in encoded  # Hide database names.
        assert "synthetic_audit_writer" not in encoded  # Hide configured role names.
