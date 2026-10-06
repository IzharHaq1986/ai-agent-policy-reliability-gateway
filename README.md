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
Dependencies and versions have not yet been selected.

Additional infrastructure requires a demonstrated requirement.

## Current status

Repository bootstrap and design are in progress.
Application code, tests, CI, and deployment are not implemented.

This project is independent of the n8n Engineering Playground.
Prior-project test results do not establish validation for this service.

## Engineering controls

Use isolated changes, independent validation, and pull-request review.
Keep secrets and local environment files outside version control.
Treat external inputs and model responses as untrusted.
