"""Explicit startup composition for PostgreSQL-audited policy evaluation."""

import os  # Read configuration supplied by trusted application setup.

from fastapi import FastAPI  # Describe the application returned by the factory.
from psycopg import ProgrammingError  # Identify invalid connection-string syntax.
from psycopg.conninfo import conninfo_to_dict  # Check syntax without connecting.

from gateway.api import create_app  # Preserve existing authentication and HTTP rules.
from gateway.postgresql_audit_receiver import (  # Reuse transactional audit delivery.
    create_postgresql_audit_receiver,  # Configure the existing database receiver.
)


def create_postgresql_app() -> FastAPI:  # Provide an explicit Uvicorn factory.
    """Require audit configuration without checking database readiness."""
    conninfo = os.environ.get("GATEWAY_AUDIT_CONNINFO")  # Read settings on each call.
    if conninfo is None or not conninfo.strip():  # Reject absent or blank settings.
        raise RuntimeError("Invalid audit database configuration")  # Hide details.

    try:  # Check syntax before configuring the receiver.
        conninfo_to_dict(conninfo)  # Parse locally; do not open a connection.
    except (ProgrammingError, ValueError):  # Normalize documented parsing failures.
        raise RuntimeError("Invalid audit database configuration") from None

    receiver = create_postgresql_audit_receiver(conninfo)  # Configure delivery only.
    return create_app(audit_sink=receiver)  # Reuse credential checks and audit gates.
