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
443 tests: 56 policy tests, 43 API tests,
91 audit-builder tests, 30 API audit-integration tests,
42 JSON-lines receiver tests, 13 API/receiver composition tests,
26 PostgreSQL receiver unit tests, 7 PostgreSQL integration tests,
12 API/PostgreSQL composition tests, 48 evaluation tests,
23 evaluation CLI tests, 7 CLI failure-path tests,
12 artifact-generation tests, 9 artifact-identity tests,
16 artifact CLI acceptance tests, and 8 artifact CLI process tests.
CI covers repository hygiene, lint, formatting,
strict source typing, and the full test suite with warnings treated as errors.

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

The builder performs no I/O. The API can optionally deliver its events
to a receiver supplied by trusted application code.
Event generation does not provide persistence, tamper resistance,
source authenticity, or complete request audit coverage.

## Optional API audit delivery

`create_app(audit_sink=receiver)` enables policy decision event delivery.
The receiver must be a synchronous callable accepting one `AuditEvent`
and returning `None`. Non-callable and asynchronous receivers are
rejected during application creation.

The application generates event identity and UTC occurrence time.
A configured receiver runs in the Starlette thread pool and is awaited
once before returning the normal policy response.

Delivery covers completed ALLOW, DENY, and policy-level invalid-request
evaluations. Failures before evaluation produce no policy decision event.
Builder or receiver failures return the existing generic HTTP 500 error.
No retry or queue is provided.

`create_app()` leaves auditing disabled. The documented Uvicorn factory
command does not configure a receiver.

A receiver returning successfully does not prove durable persistence.
It may accept an event and then fail; delivery is not transactional.
Blocking receivers have no application-level timeout or cancellation
guarantee. No default receiver, retention, or tamper resistance is supplied.

## JSON-lines audit receiver

`gateway.audit_receiver.create_audit_receiver(stream)` returns a
synchronous receiver for an explicitly supplied text stream.

Each event is validated against the exact nine-field contract before
writing. Policy field validation reuses the existing audit builder.
Accepted events become compact JSON with sorted keys and one trailing
newline. Inputs are not modified.

A receiver-owned lock coordinates each write and flush. Coordination
applies only to calls through the same receiver instance, not separate
instances or processes. The receiver does not open or close the stream.

The receiver requires a complete write and successful flush before
returning `None`. Write, short-write, and flush failures propagate.
Partial output may remain after failure; no retry or rollback is provided.

Successful flush does not establish durable persistence. Stream calls
may block. Destination access controls, retention, and operational
configuration remain the caller's responsibility.

The receiver is not automatically wired into the API. The documented
Uvicorn command continues to run with auditing disabled.

In-process composition tests connect the real API, policy evaluator,
audit builder, and receiver. They verify event output and generic HTTP
500 responses for write, short-write, and flush failures. These tests
do not establish live-server behavior or durable persistence.

## PostgreSQL audit schema

`migrations/001_policy_decision_audit.sql` creates
`gateway_audit.policy_decisions` with exactly the nine audit event fields.
Constraints enforce supported values, decision/reason consistency,
canonical UUID v4 syntax, valid UTC calendar timestamps, and unique IDs.

The migration requires pre-provisioned `gateway_audit_owner`,
`gateway_audit_writer`, and `gateway_audit_reader` roles.
The owner is separate from application roles. Writer and reader roles
must not inherit ownership or administrative privileges.

The writer receives schema usage plus table insert and select access.
The reader receives schema usage plus table select access.
No audit access is granted to PUBLIC. Database connection permissions
and login credentials are provisioned separately.

Apply with `psql -X -v ON_ERROR_STOP=1` using a migration administrator
and the numbered SQL file. The migration is transactional and deliberately
fails on repeat application. It does not create roles or credentials.

`tests/sql/test_policy_decision_audit.sql` exercises constraints and
effective role privileges in isolated storage. Its test rows are rolled
back. Local PostgreSQL 18.6 validation also verified failed-migration
atomicity and preservation of data after repeat application.

The required CI job runs the SQL acceptance file after migration and
before the Python suite against disposable PostgreSQL storage.
The runner requires the SQL success marker and verifies that its test rows
were rolled back. SQL failures stop validation before Python tests run.
Schema validation does not establish recovery, retention, or production
readiness. The API is not configured with a database receiver by default.

## PostgreSQL audit receiver

`gateway.postgresql_audit_receiver.create_postgresql_audit_receiver(conninfo)`
returns a synchronous receiver for trusted connection configuration.
It reuses shared audit-event validation before opening a connection.

Each invocation opens one connection and uses a READ COMMITTED transaction
with parameterized SQL. It sets synchronous_commit=on, lock_timeout=2s,
and statement_timeout=5s; connection establishment uses connect_timeout=5.
These settings do not provide a total invocation deadline.

A successful delivery returns None after commit acknowledgement and
connection closure. An identical event ID and nine-field event is accepted
without another row. Different content for an existing ID raises
AuditConflictError without updating the stored event. No retries occur.

Failures propagate to the caller. Cleanup attempts preserve the original
delivery failure. A commit acknowledgement failure can leave a committed
event; an explicit retry must reuse the original ID and content.
Commit acknowledgement assumes correctly configured database durability.
It does not establish backups, recovery, retention, or tamper resistance.

The receiver requires the separately provisioned writer role. It does not
create roles, apply migrations, manage credentials, or wire itself into
the API. The documented Uvicorn factory still runs with auditing disabled.

Run all tests against an isolated PostgreSQL instance:

```bash
docker pull --platform linux/amd64 \
    docker.io/library/postgres@sha256:885953109528ad3dfc90362b1a6f50a78620b5315be19f187753d267e484dc5b
PYTHONDONTWRITEBYTECODE=1 venv/bin/python tests/support/run_postgresql_acceptance.py
```

The runner uses a temporary volume, generated test credentials, separate
authenticated writer and reader roles, and a loopback-only published port.
It removes its container, volume, and temporary credential file on normal
completion or handled failure. Forced termination can leave resources.

Integration tests cover committed visibility, duplicate and conflict
handling, concurrency, lock timeout, and a real slow-statement timeout.
The ordinary pytest command skips the 19 database-dependent tests unless
explicit test connections are supplied. The required CI job uses the
isolated runner.

API/PostgreSQL composition tests verify committed events through a separate
reader connection for ALLOW, DENY, and policy-level invalid requests.
Pre-policy failures persist no event. Real database insertion permission
failures return the existing generic HTTP 500 response.

These are in-process HTTP tests. They do not establish live-server behavior,
crash recovery, or production readiness. Default API auditing remains disabled.

## Deterministic policy evaluation

`gateway.evaluation.evaluate_dataset(dataset)` evaluates a validated
scenario collection against the existing deterministic policy core.

`evaluations/policy_core_v1.json` defines 12 synthetic scenarios across
six categories with explicit expected results. The runner validates the
entire dataset before evaluating scenarios and compares all four policy
result fields.

Reports include total, passed, failed, pass rate, category counts, and
per-scenario mismatch fields. Invalid datasets raise `EvaluationDataError`;
unexpected evaluator failures propagate. The runner performs no I/O,
does not modify inputs, and makes no model calls or tool invocations.

Acceptance tests require every committed scenario to match its expected
result and verify that deliberate mismatches are reported as failures.
The existing pytest suite includes this evaluation gate.

These results measure deterministic policy behavior. Synthetic instruction
cases do not establish live-model prompt-injection resistance, groundedness,
citation validity, or production release safety.

## Evaluation command-line interface

Run from the repository root with the development environment installed:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src venv/bin/python -m gateway.evaluation_cli evaluations/policy_core_v1.json
```

The command accepts exactly one dataset path and reads at most 1 MiB
plus one byte to detect oversized input. It requires UTF-8 JSON and
reuses the API's duplicate-key and non-finite constant rejection hooks.
Dataset validation and evaluation reuse the existing pure runner.

| Exit code | Meaning |
| --- | --- |
| 0 | Every scenario matches its expected result |
| 1 | At least one scenario mismatches |
| 2 | Usage, input, evaluation, or output failure |

Valid evaluations write one compact JSON report with sorted keys and a
trailing newline to stdout. Failures write a generic code to stderr:
USAGE_ERROR, INPUT_ERROR, EVALUATION_ERROR, or OUTPUT_ERROR.
Paths, input content, and exception details are not included in errors.

The command does not modify datasets, create report files, or retry.
An output failure can leave partial output; consumers must check the
exit code. After an output error, the CLI attempts to redirect stdout to
the operating system's discard destination to prevent shutdown from
retrying the failed stream. A real closed-pipe regression test verifies
exit 2 with only OUTPUT_ERROR on stderr. Tests also cover write and flush
failures and real-process usage and input errors.

The byte limit does not provide an I/O timeout.
Results measure synthetic deterministic policy behavior and do not
establish live-model or production safety.

## Evaluation evidence artifacts

`gateway.evaluation_artifact.generate_evaluation_artifact(dataset_path)`
returns a versioned evidence artifact using the existing evaluation runner.
The existing CLI report format and exit behavior remain unchanged.

The artifact contains exactly 12 fields: schema_version, evaluation_type,
dataset_schema_version, dataset_sha256, code_revision, code_worktree_clean,
occurred_at, executed, skipped, failed, report, and limitations.

Dataset input is read once with the existing 1 MiB limit and JSON validation.
SHA-256 identifies the exact captured bytes evaluated, including whitespace.
The nested report preserves the existing deterministic report contract.
Executed counts all evaluated scenarios; skipped is zero; failed counts
result mismatches. Invalid input or evaluator failure produces no artifact.

Code identity uses the repository containing the implementation.
Generation requires a committed HEAD and a clean Git worktree, including
staged changes and untracked files that are not ignored.
Git revision and cleanliness are checked before and after evaluation.
Ignored local planning and virtual-environment files are outside this check.

UTC occurrence time is generated by trusted application code.
Failures use ArtifactGenerationError with INPUT_ERROR, EVALUATION_ERROR,
or CODE_IDENTITY_ERROR, without including paths, content, or exception details.
The function returns an artifact in memory; it does not save a report file.

Git observations do not provide an atomic code snapshot or detect hostile
changes restored between checks. Dependency versions are not identified.
Hashes and revision identifiers do not establish authenticity.
Evidence covers synthetic deterministic policy behavior only, with no
live-model evaluation or production safety guarantee.

## Evaluation artifact command

Run from a clean committed checkout with the development environment installed:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src venv/bin/python -m gateway.evaluation_artifact_cli evaluations/policy_core_v1.json
```

The command accepts exactly one dataset path and invokes the existing
artifact generator once. It writes one compact JSON artifact with sorted
keys and a trailing newline to stdout.

| Exit code | Meaning |
| --- | --- |
| 0 | Complete artifact delivered with zero mismatches |
| 1 | Complete artifact delivered with one or more mismatches |
| 2 | Usage, generation, serialization, or output failure |

Errors use generic stderr codes: USAGE_ERROR, INPUT_ERROR,
CODE_IDENTITY_ERROR, EVALUATION_ERROR, SERIALIZATION_ERROR, or OUTPUT_ERROR.
Unexpected generation failures do not expose exception details.
Serialization completes before output begins.

Both evaluation commands share stdout delivery and broken-pipe recovery.
A full write and successful flush are required. Short writes now produce
OUTPUT_ERROR and exit 2 in the existing evaluation CLI as well.

The command does not create report files. If redirecting stdout, use a
destination outside the checkout and inspect the exit code before accepting
the evidence. Failed delivery can leave empty or partial output.
Creating an untracked report inside the checkout can invalidate code
identity checks. Flush success does not prove durable storage.
If stderr is unavailable, error-message delivery cannot be guaranteed.

The existing evaluation command retains its report format and normal
exit behavior. Artifact evidence remains synthetic and model-free;
Git observations are non-atomic and do not establish authenticity.

## Engineering controls

Use isolated changes, independent validation, and pull-request review.
Keep secrets and local environment files outside version control.
Treat external inputs and model responses as untrusted.
