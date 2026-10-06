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
56 acceptance tests. CI covers repository hygiene, lint, formatting,
strict policy-module typing, and policy tests.

FastAPI, authentication, tool execution, approvals, persistence,
live-model evaluations, and deployment are not implemented.

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
    -r requirements-dev.txt
venv/bin/python -m ruff check --no-cache src tests
venv/bin/python -m ruff format --no-cache --check src tests
venv/bin/python -m mypy --cache-dir venv/.mypy_cache
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -q
```

Direct validation tools are pinned; transitive dependencies are not
locked. These tests do not establish production or live-model safety.

## Engineering controls

Use isolated changes, independent validation, and pull-request review.
Keep secrets and local environment files outside version control.
Treat external inputs and model responses as untrusted.
