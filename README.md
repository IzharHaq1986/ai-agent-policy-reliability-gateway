# AI Agent Policy & Reliability Gateway

An independent Python service for deterministic policy enforcement
between AI applications and operational tools.

## Objective

Build explicit authentication, authorization, validation, evidence
provenance, human-approval gates, and auditable execution boundaries.

AI-generated recommendations remain advisory. Deterministic service
controls retain operational authority.

## Initial implementation scope

Evaluate synthetic structured tool requests against server-owned policy.

- Default to denial unless a request is explicitly permitted.
- Keep trusted caller identity separate from request-supplied claims.
- Return a policy decision, reason code, and policy version.
- Keep execution status `not_executed` for every initial outcome.
- Make no model calls or operational tool invocations.

## Planned technology

Python, FastAPI, PostgreSQL, Docker, pytest, and GitHub Actions.
Application dependencies remain unselected. Development validation
tool versions are pinned in requirements-dev.txt.

Additional infrastructure requires a demonstrated requirement.

## Current status

The repository includes a synthetic deterministic policy core and
190 acceptance tests: 56 policy tests, 43 API tests, and 91 audit tests. CI covers repository hygiene, lint, formatting,
strict source typing, and policy/API tests with warnings treated as errors.

A local FastAPI endpoint authenticates one server-configured Bearer
credential and evaluates requests without executing tools.

Tool execution, approvals, persistence, live-model evaluations,
production identity management, and deployment are not implemented.

This project is independent of the n8n Engineering Playground.
Prior-project test results do not establish validation for this service.

## Policy interface

`gateway.policy.evaluate_request(request, *, principal_id)` returns
a decision, reason code, policy version, and `not_executed` status.

The caller must supply principal identity through a trusted boundary.
The function does not authenticate an identity or execute a tool.
An ALLOW decision is not permission to bypass later execution gates.

## Local validation

Use Python 3.12 and an isolated virtual environment:

```bash
python3 -m venv venv
venv/bin/python -m pip --isolated install \
    --index-url https://pypi.org/simple --only-binary=:all: \
    -r requirements.txt -r requirements-dev.txt
venv/bin/python -m ruff check --no-cache src tests
venv/bin/python -m ruff format --no-cache --check src tests
venv/bin/python -m mypy --cache-dir venv/.mypy_cache
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -q -W error
```

Direct validation tools are pinned; transitive dependencies are not
locked. These tests do not establish production or live-model safety.

## Local authenticated API

`POST /v1/policy/evaluate` accepts the existing policy request body.
The configured credential maps to `reliability-reader`; callers cannot
select an identity through request fields or headers.

Authentication precedes body reading. Accepted bodies are limited to
4096 bytes, including streamed requests. The endpoint requires JSON
and rejects duplicate keys and non-finite JSON constants.

| HTTP status | Meaning |
| --- | --- |
| 200 | Valid request; ALLOW or DENY decision, always not_executed |
| 401 | Missing, malformed, or invalid credential |
| 413 | Body exceeds the limit |
| 415 | Unsupported media type |
| 422 | Invalid JSON, incomplete body, or invalid policy input |
| 500 | Generic unexpected failure |

Documentation endpoints are disabled. Missing or malformed server
credential configuration prevents application creation.

After installing dependencies, run locally with a generated credential:

```bash
export GATEWAY_API_TOKEN="$(venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
venv/bin/python -m uvicorn gateway.api:create_app \
    --factory --app-dir src --host 127.0.0.1 --port 8000
```

Clients must supply the configured credential as a Bearer token.
Do not print, commit, or include actual credentials in reports.
The initial credential has no built-in expiry or revocation service.
Public exposure, TLS, rate limiting, server timeouts, and deployment
require separate design and validation.

## Policy decision audit events

`gateway.audit.build_audit_event(policy_result, *, principal_id,
event_id, occurred_at)` validates a policy result and returns a new
nine-field audit event without modifying its inputs.

The caller supplies trusted context: the server-owned
`reliability-reader` identity, a canonical UUID version 4, and a valid
UTC timestamp formatted `YYYY-MM-DDTHH:MM:SSZ`.

The builder reuses the existing policy contract and reason codes.
It rejects extra or missing result fields, incorrect types,
inconsistent decisions, and unsupported policy or execution states
with `AuditValidationError`.

Events contain only schema version, event type, event ID, occurrence
time, principal identity, decision, reason code, policy version, and
execution status. Request content and credentials are not retained.
All accepted events retain `execution_status=not_executed`.

The builder performs no I/O and is not integrated into the API.
Event generation does not provide persistence, tamper resistance,
source authenticity, or complete request audit coverage.

## Engineering controls

Use isolated changes, independent validation, and pull-request review.
Keep secrets and local environment files outside version control.
Treat external inputs and model responses as untrusted.
