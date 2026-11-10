"""Explicit Docker runner for isolated PostgreSQL acceptance tests."""

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from psycopg import sql
from psycopg.conninfo import make_conninfo

IMAGE = (
    "docker.io/library/postgres@sha256:"
    "885953109528ad3dfc90362b1a6f50a78620b5315be19f187753d267e484dc5b"
)
ROOT = Path(__file__).resolve().parents[2]


def docker(*args, input_text=None):
    result = subprocess.run(
        ["docker", *args],
        input=input_text,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Isolated Docker validation operation failed")
    return result.stdout


def report_startup_failure(container, reason, attempts, started):
    """Emit restricted observations without replacing an ordinary startup failure."""
    try:
        if reason not in {"probe_timeout", "probe_error", "observation_expired"}:
            return  # Never print arbitrary reason text.
        if type(attempts) is not int or attempts < 0:
            return
        elapsed = time.monotonic() - started
        if not 0 <= elapsed < float("inf"):
            return  # Reject invalid timing rather than formatting untrusted values.
        print(
            f"POSTGRESQL_STARTUP_FAILURE={reason} "
            f"PROBES={attempts} ELAPSED_SECONDS={elapsed:.2f}"
        )
        try:
            inspected = subprocess.run(
                ["docker", "inspect", "--format", "{{json .State}}", container],
                capture_output=True,
                text=True,
                timeout=5,
            )  # Inspect only the owned container, once, before existing cleanup.
            if inspected.returncode != 0:
                raise ValueError("Inspection unavailable")
            state = json.loads(inspected.stdout)
            if type(state) is not dict:
                raise ValueError("Invalid inspection state")
            status = state.get("Status")
            exit_code = state.get("ExitCode")
            oom = state.get("OOMKilled")
            if (
                type(status) is not str
                or status
                not in {
                    "created",
                    "running",
                    "paused",
                    "restarting",
                    "removing",
                    "exited",
                    "dead",
                }
                or type(exit_code) is not int
                or not -(2**31) <= exit_code < 2**31
                or type(oom) is not bool
            ):
                raise ValueError("Invalid inspection fields")
        except Exception:
            print("POSTGRESQL_STARTUP_INSPECTION=unavailable")
        else:
            print(
                f"POSTGRESQL_STARTUP_INSPECTION=available STATUS={status} "
                f"EXIT_CODE={exit_code} OOM={str(oom).lower()}"
            )  # Exclude raw output, errors, environment, mounts, and credentials.
    except Exception:
        pass  # Ordinary diagnostic failures must not replace the startup failure.


def wait_for_postgresql(container):
    """Preserve existing readiness rules while reporting ordinary failures."""
    started = time.monotonic()
    deadline = started + 40
    attempts = 0
    reason = "probe_error"
    try:
        while True:
            attempts += 1
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "gateway_test_admin",
                    "-d",
                    "gateway_audit_test",
                ],
                capture_output=True,
                timeout=5,
            )
            if ready.returncode == 0:
                return  # Preserve successful readiness without extra inspection.
            if time.monotonic() >= deadline:
                reason = "observation_expired"
                raise RuntimeError("Isolated PostgreSQL readiness timed out")
            time.sleep(0.5)  # Preserve the existing polling interval.
    except subprocess.TimeoutExpired:
        report_startup_failure(container, "probe_timeout", attempts, started)
        raise  # Preserve the exact original timeout; do not retry the probe.
    except Exception:
        report_startup_failure(container, reason, attempts, started)
        raise  # Existing main-level cleanup still runs before the failure propagates.


def main():
    suffix = uuid4().hex
    container = f"gateway-receiver-test-{suffix}"
    volume = f"gateway-receiver-test-{suffix}"
    volume_created = False

    with tempfile.TemporaryDirectory(prefix="gateway-receiver-") as temporary:
        password_file = Path(temporary) / "bootstrap-password"
        password_file.write_text(secrets.token_urlsafe(32))
        password_file.chmod(0o444)
        try:
            docker("image", "inspect", IMAGE)
            docker("volume", "create", volume)
            volume_created = True
            docker(
                "run",
                "--detach",
                "--name",
                container,
                "--platform",
                "linux/amd64",
                "--security-opt",
                "no-new-privileges=true",
                "--cpus",
                "1",
                "--memory",
                "512m",
                "--pids-limit",
                "128",
                "--publish",
                "127.0.0.1::5432",
                "--mount",
                f"type=volume,src={volume},dst=/var/lib/postgresql",
                "--mount",
                f"type=bind,src={password_file},dst=/run/bootstrap-password,readonly",
                "--env",
                "POSTGRES_PASSWORD_FILE=/run/bootstrap-password",
                "--env",
                "POSTGRES_USER=gateway_test_admin",
                "--env",
                "POSTGRES_DB=gateway_audit_test",
                IMAGE,
                "-c",
                "max_connections=20",
                "-c",
                "shared_buffers=32MB",
            )
            wait_for_postgresql(container)

            def execute_sql(statement):
                return docker(
                    "exec",
                    "-i",
                    container,
                    "psql",
                    "-X",
                    "-A",
                    "-t",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "gateway_test_admin",
                    "-d",
                    "gateway_audit_test",
                    input_text=statement,
                )

            details = json.loads(docker("inspect", container))[0]
            bindings = details["NetworkSettings"]["Ports"]["5432/tcp"]
            assert len(bindings) == 1
            assert bindings[0]["HostIp"] == "127.0.0.1"
            port = bindings[0]["HostPort"]
            passwords = {
                role: secrets.token_urlsafe(32)
                for role in (
                    "gateway_audit_writer",
                    "gateway_audit_reader",
                    "gateway_approval_proof_writer",
                    "gateway_approval_proof_reader",
                )
            }
            # Provision the owners and roles used by the SQL-only request suite.
            for role in (
                "gateway_audit_owner",
                "gateway_approval_owner",
                "gateway_approval_request_writer",
                "gateway_approval_request_reader",
            ):
                execute_sql(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS;"
                    )
                    .format(sql.Identifier(role))
                    .as_string()
                )
            for role, password in passwords.items():
                execute_sql(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS PASSWORD {};"
                    )
                    .format(sql.Identifier(role), sql.Literal(password))
                    .as_string()
                )
            execute_sql(
                "REVOKE CONNECT ON DATABASE gateway_audit_test FROM PUBLIC;"
                "GRANT CONNECT ON DATABASE gateway_audit_test "
                "TO gateway_audit_writer, gateway_audit_reader;"
            )
            for migration in (  # Apply prerequisites before dependent tables.
                "migrations/001_policy_decision_audit.sql",  # Create policy storage.
                "migrations/002_request_bindings.sql",  # Create linked request storage.
                "migrations/003_execution_events.sql",  # Create execution storage.
                "migrations/004_protected_approval_requests.sql",
            ):  # Keep the migration order explicit.
                execute_sql(
                    (ROOT / migration).read_text()
                )  # Apply in this test database.

            for acceptance_file, marker in (  # Run each SQL suite.
                (  # Preserve the existing policy-schema checks.
                    "tests/sql/test_policy_decision_audit.sql",
                    "AUDIT_SCHEMA_SQL_ACCEPTANCE=PASS",
                ),
                (  # Add the new binding-schema checks.
                    "tests/sql/test_request_bindings.sql",
                    "REQUEST_BINDING_SQL_ACCEPTANCE=PASS",
                ),
                (  # Check execution-event storage and permissions.
                    "tests/sql/test_execution_events.sql",
                    "EXECUTION_EVENT_SQL_ACCEPTANCE=PASS",
                ),
                (
                    "tests/sql/test_protected_approval_requests.sql",
                    "PROTECTED_APPROVAL_REQUEST_SQL_ACCEPTANCE=PASS",
                ),
            ):  # Require each suite's own success marker.
                sql_acceptance_output = execute_sql(  # Stop on any SQL failure.
                    (ROOT / acceptance_file).read_text()  # Read the selected suite.
                )  # Each suite rolls back its fixtures.
                if (
                    marker not in sql_acceptance_output.splitlines()
                ):  # Check completion.
                    raise RuntimeError("SQL acceptance success marker missing")
                remaining_rows = execute_sql(  # Check all tables after each suite.
                    "SELECT count(*) FROM gateway_audit.policy_decisions "
                    "UNION ALL "
                    "SELECT count(*) FROM gateway_audit.request_bindings "
                    "UNION ALL "
                    "SELECT count(*) FROM gateway_audit.execution_events "
                    "UNION ALL "
                    "SELECT count(*) FROM gateway_approval.requests;"
                ).splitlines()  # Preserve the individual row counts.
                if remaining_rows != ["0", "0", "0", "0"]:
                    raise RuntimeError("SQL acceptance left persistent test rows")
                print(marker)  # Report only a completed and cleaned SQL suite.
            print(
                "SQL_ACCEPTANCE_TEST_ROWS=ROLLED_BACK"
            )  # Confirm the acceptance tables are empty.
            settings = execute_sql(
                "SELECT current_setting('fsync'), "
                "current_setting('full_page_writes'), "
                "current_setting('synchronous_commit');"
            ).strip()
            assert settings == "on|on|on"

            execute_sql(
                (ROOT / "tests/sql/approval_consumption_proof_fixture.sql").read_text()
            )
            # Give the dedicated test roles access only to the proof fixture.
            execute_sql(
                "GRANT CONNECT ON DATABASE gateway_audit_test "
                "TO gateway_approval_proof_writer, gateway_approval_proof_reader;"
                "GRANT USAGE ON SCHEMA approval_consumption_proof "
                "TO gateway_approval_proof_writer, gateway_approval_proof_reader;"
                "GRANT SELECT ON approval_consumption_proof.approvals "
                "TO gateway_approval_proof_writer, gateway_approval_proof_reader;"
                "GRANT UPDATE (state, execution_id) "
                "ON approval_consumption_proof.approvals "
                "TO gateway_approval_proof_writer;"
                "GRANT SELECT, UPDATE (active) "
                "ON approval_consumption_proof.scope_grants "
                "TO gateway_approval_proof_writer;"
                "GRANT INSERT ON approval_consumption_proof.consumption_events "
                "TO gateway_approval_proof_writer;"
                "GRANT SELECT ON approval_consumption_proof.consumption_events "
                "TO gateway_approval_proof_reader;"
            )

            environment = os.environ.copy()
            for variable, role in (
                ("GATEWAY_TEST_WRITER_DSN", "gateway_audit_writer"),
                ("GATEWAY_TEST_READER_DSN", "gateway_audit_reader"),
                (
                    "GATEWAY_TEST_APPROVAL_PROOF_WRITER_DSN",
                    "gateway_approval_proof_writer",
                ),
                (
                    "GATEWAY_TEST_APPROVAL_PROOF_READER_DSN",
                    "gateway_approval_proof_reader",
                ),
            ):
                environment[variable] = make_conninfo(
                    host="127.0.0.1",
                    port=port,
                    dbname="gateway_audit_test",
                    user=role,
                    password=passwords[role],
                )
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-W", "error", "--tb=short"],
                cwd=ROOT,
                env=environment,
                timeout=120,
            )
            if completed.returncode:
                raise RuntimeError("PostgreSQL acceptance suite failed")
            print("AUTHENTICATED_POSTGRESQL_ACCEPTANCE=PASS")
            print("PUBLISHED_ADDRESS=LOOPBACK_ONLY")
        finally:
            exists = subprocess.run(
                ["docker", "inspect", container], capture_output=True, timeout=10
            )
            if exists.returncode == 0:
                docker("rm", "--force", container)
            if volume_created:
                docker("volume", "rm", volume)
            print("CONTAINER_AND_VOLUME_CLEANUP=COMPLETE")
    print("TEMPORARY_CREDENTIAL_FILE=REMOVED")


if __name__ == "__main__":
    main()
