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
            deadline = time.monotonic() + 40
            while True:
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
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Isolated PostgreSQL readiness timed out")
                time.sleep(0.5)

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
                for role in ("gateway_audit_writer", "gateway_audit_reader")
            }
            execute_sql(
                "CREATE ROLE gateway_audit_owner NOLOGIN NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;"
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
            execute_sql((ROOT / "migrations/001_policy_decision_audit.sql").read_text())
            settings = execute_sql(
                "SELECT current_setting('fsync'), "
                "current_setting('full_page_writes'), "
                "current_setting('synchronous_commit');"
            ).strip()
            assert settings == "on|on|on"

            environment = os.environ.copy()
            for variable, role in (
                ("GATEWAY_TEST_WRITER_DSN", "gateway_audit_writer"),
                ("GATEWAY_TEST_READER_DSN", "gateway_audit_reader"),
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
