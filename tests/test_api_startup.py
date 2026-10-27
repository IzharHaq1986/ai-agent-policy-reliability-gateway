"""Configuration and HTTP checks for PostgreSQL-audited startup."""

import importlib  # Verify module import performs no database access.

import psycopg  # Block accidental real database connections.
import pytest  # Supply isolated configuration and expected failures.
from fastapi.testclient import TestClient  # Exercise the existing HTTP boundary.

from gateway import api_startup as module  # Test the new composition factory.
from gateway.api import create_app  # Verify the original factory remains unchanged.

TOKEN = "synthetic_startup_credential_1234567890"  # Use a test-only credential.
CONNINFO = "dbname=synthetic_audit user=synthetic_writer"  # Use test-only settings.
HEADERS = {"Authorization": f"Bearer {TOKEN}"}  # Authenticate test requests.


@pytest.fixture(autouse=True)
def configuration(monkeypatch):  # Isolate configuration and prohibit real connections.
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)  # Supply a valid test credential.
    monkeypatch.setenv("GATEWAY_AUDIT_CONNINFO", CONNINFO)  # Supply valid syntax.

    def forbidden(*args, **kwargs):  # Detect premature database access.
        pytest.fail("Startup tests must not connect to PostgreSQL")

    monkeypatch.setattr(psycopg, "connect", forbidden)  # Block every real connection.


def payload():  # Return an independent supported request.
    return {
        "tool": "fixture_store",  # Select the existing synthetic tool.
        "operation": "READ_ITEM",  # Request the supported operation.
        "arguments": {"item_id": 42},  # Use an allowed synthetic item.
    }


@pytest.mark.parametrize("value", [None, "", " ", "\t\n", "private-invalid-setting"])
def test_invalid_configuration_is_generic(monkeypatch, value):  # Reject bad settings.
    if value is None:  # Exercise an absent environment variable.
        monkeypatch.delenv("GATEWAY_AUDIT_CONNINFO")
    else:  # Exercise empty or malformed supplied settings.
        monkeypatch.setenv("GATEWAY_AUDIT_CONNINFO", value)
    with pytest.raises(RuntimeError) as caught:  # Require a startup failure.
        module.create_postgresql_app()
    assert str(caught.value) == "Invalid audit database configuration"
    assert caught.value.__suppress_context__ or caught.value.__context__ is None


def test_import_and_creation_do_not_connect():  # Configuration is not readiness.
    importlib.reload(module)  # Repeat module initialization with connections blocked.
    app = module.create_postgresql_app()  # Construct the real receiver and API.
    assert app is not None  # Creation succeeds without contacting PostgreSQL.


@pytest.mark.parametrize("token", [None, "", "invalid"])
def test_existing_credential_validation_remains(
    monkeypatch, token
):  # Reuse API checks.
    if token is None:  # Test missing credentials.
        monkeypatch.delenv("GATEWAY_API_TOKEN")
    else:  # Test malformed credentials.
        monkeypatch.setenv("GATEWAY_API_TOKEN", token)
    with pytest.raises(
        RuntimeError, match="^Invalid gateway credential configuration$"
    ):
        module.create_postgresql_app()  # Do not bypass existing authentication setup.


def test_factory_reads_configuration_on_each_call(
    monkeypatch,
):  # Avoid cached settings.
    supplied = []  # Observe configuration passed to the receiver factory.

    def receiver_factory(
        conninfo,
    ):  # Replace persistence without duplicating API rules.
        supplied.append(conninfo)  # Preserve the exact supplied configuration.
        return lambda event: None  # Acknowledge controlled test delivery.

    monkeypatch.setattr(module, "create_postgresql_audit_receiver", receiver_factory)
    module.create_postgresql_app()  # Read the initial configuration.
    second = "service=synthetic_reader_test"  # Use another syntactically valid value.
    monkeypatch.setenv("GATEWAY_AUDIT_CONNINFO", second)  # Change trusted setup.
    module.create_postgresql_app()  # Read the replacement configuration.
    assert supplied == [CONNINFO, second]  # Neither value is cached or rewritten.


@pytest.mark.parametrize(
    ("change", "status", "decision"),
    [
        ({}, 200, "ALLOW"),
        ({"tool": "unknown"}, 200, "DENY"),
        ({"arguments": {"item_id": 0}}, 422, "DENY"),
    ],
)
def test_decisions_use_configured_receiver(monkeypatch, change, status, decision):
    events = []  # Capture delivered policy events.
    monkeypatch.setattr(
        module, "create_postgresql_audit_receiver", lambda conninfo: events.append
    )  # Use the real API with controlled audit delivery.
    request = payload()  # Start with valid input.
    request.update(change)  # Exercise the selected policy outcome.
    with TestClient(module.create_postgresql_app()) as client:  # Use new startup.
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=request)
    assert response.status_code == status  # Preserve existing HTTP semantics.
    assert len(events) == 1  # Require one audit delivery.
    assert events[0]["decision"] == decision  # Record the evaluated outcome.
    assert events[0]["execution_status"] == "not_executed"  # Never execute tools.


def test_delivery_failure_returns_generic_error(monkeypatch):  # Do not fail open.
    calls = []  # Detect automatic retries.

    def fail(event):  # Simulate a persistence failure.
        calls.append(event)  # Record the one delivery attempt.
        raise RuntimeError(
            "private-database-detail"
        )  # Keep sensitive details internal.

    monkeypatch.setattr(
        module, "create_postgresql_audit_receiver", lambda conninfo: fail
    )  # Configure controlled failure.
    with TestClient(
        module.create_postgresql_app(), raise_server_exceptions=False
    ) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=payload())
    assert response.status_code == 500  # Withhold the normal policy response.
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}  # Hide details.
    assert len(calls) == 1  # Do not retry or fall back.


def test_original_factory_ignores_new_audit_setting(monkeypatch):  # Preserve defaults.
    monkeypatch.setenv("GATEWAY_AUDIT_CONNINFO", "invalid-private-setting")
    with TestClient(create_app()) as client:  # Use the original unaudited factory.
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=payload())
    assert response.status_code == 200  # Preserve existing startup behavior.
    assert response.json()["execution_status"] == "not_executed"
