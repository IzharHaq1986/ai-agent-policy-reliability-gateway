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
1568 tests: 56 policy tests, 43 API tests,
91 audit-builder tests, 30 API audit-integration tests,
42 JSON-lines receiver tests, 13 API/receiver composition tests,
26 PostgreSQL receiver unit tests, 7 PostgreSQL integration tests,
12 API/PostgreSQL composition tests, 48 evaluation tests,
23 evaluation CLI tests, 7 CLI failure-path tests,
12 artifact-generation tests, 9 artifact-identity tests,
16 artifact CLI acceptance tests, 8 artifact CLI process tests,
20 fixture simulator tests, 156 execution-event contract tests,
21 execution-event receiver tests, 59 fixture-result tests,
78 request-binding contract tests, 79 request-snapshot tests,
45 paired-receiver unit tests, 8 paired-receiver integration tests,
52 execution-receiver unit tests, 19 execution-receiver integration tests,
237 execution-result contract tests, 73 simulator-orchestration unit tests,
17 simulator-orchestration integration tests, 15 API-startup unit tests,
7 API-startup PostgreSQL integration tests,
7 audited API real-process tests,
114 synthetic provenance contract tests,
74 execution-evidence contract tests, 34 simulator-evidence unit tests,
and 10 simulator-evidence PostgreSQL integration tests.
CI covers repository hygiene, lint, formatting,
strict source typing, and the full test suite with warnings treated as errors.

A local FastAPI endpoint authenticates one server-configured Bearer
credential and evaluates requests without executing tools.

An explicit opt-in Python executor can invoke the fixed synthetic simulator
after internal policy evaluation and required PostgreSQL audit delivery.
Policy decisions, request bindings, and execution events have PostgreSQL
persistence. The executor is not connected to the HTTP API or startup.

Real operational tool integrations, an approval lifecycle, replay prevention,
live-model evaluations, production identity management, and deployment
are not implemented.

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

## Explicit PostgreSQL-audited API startup

`gateway.api_startup.create_postgresql_app()` provides a separate,
explicitly selected startup factory. It reuses the existing API and
PostgreSQL policy-audit receiver. The original `gateway.api:create_app`
startup remains unaudited unless trusted code supplies a receiver.

The new factory requires GATEWAY_API_TOKEN and GATEWAY_AUDIT_CONNINFO
from trusted application configuration. Request bodies and headers cannot
select the database or disable audit delivery.

Missing, blank, or syntactically invalid connection configuration raises
RuntimeError with `Invalid audit database configuration`.
Underlying parsing details are suppressed. Existing API credential
validation remains unchanged.

The following command is a local startup template. First provision the
database schema and least-privilege writer credentials separately.
Replace APPROVED_WRITER_SERVICE with an approved libpq service configured
for the intended database and writer role. Supply GATEWAY_API_TOKEN through
trusted setup. Do not put passwords in commands, reports, or version control.

```bash
# Select the separately provisioned database connection service.
export GATEWAY_AUDIT_CONNINFO='service=APPROVED_WRITER_SERVICE'
# Start the explicitly audited, non-executing API on loopback.
venv/bin/python -m uvicorn gateway.api_startup:create_postgresql_app \
    --factory --app-dir src --host 127.0.0.1 --port 8000
```

Configuration is read when the factory is called. Module import and
application creation open no database connection. Successful creation
does not establish database availability, service-file correctness,
schema availability, write permission, or power-loss durability.

During evaluated requests, the existing API awaits audit delivery before
returning its normal response. Delivery failures retain the generic
HTTP 500 / INTERNAL_ERROR response. There is no unaudited fallback,
automatic retry, or alternate destination.
Failures before policy evaluation still produce no policy-decision event.

A failed delivery can leave a committed event after lost acknowledgement
or a post-commit close failure. An HTTP error does not prove that no record
was saved. Existing receiver timeout and durability limitations apply.

This factory provisions no credentials, roles, migrations, or database.
It adds no readiness endpoint, deployment configuration, or simulator
integration. HTTP policy responses retain not_executed.

### Real-process startup acceptance

`tests/test_api_startup_process.py` exercises the actual Uvicorn factory
in a separate process through real loopback HTTP connections.
The existing isolated PostgreSQL acceptance runner discovers these tests
automatically; no separate service setup or CI command is required.

The seven cases cover ALLOW, unknown-tool DENY, policy-invalid input,
missing authentication, and database write-permission failure across
three policy outcomes. A separate reader checks committed audit evidence.
Policy records retain not_executed.

The tests reserve an operating-system-selected loopback port and pass
the socket to the child process. Startup observation, HTTP operations,
and child shutdown have bounded waits. Test cleanup stops and reaps
only the server process it created.

Database identity and application roles are checked before test activity.
The child receives controlled configuration and a fresh synthetic API
credential. Raw server output stays in a private temporary capture and
is not printed. Database-dependent cases skip when explicit test DSNs
are absent; the required isolated acceptance runner supplies them.

Record correlation uses before/after event identities in the dedicated
serial test database. These checks require no concurrent external writers;
unexpected additional records fail instead of being attributed by guesswork.

Passing these tests establishes the exercised local startup, HTTP, and
persistence behavior. It does not establish production readiness,
capacity, recovery, authenticity, or power-loss durability.
No application behavior, CI configuration, or tool authority is changed.

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

## Deterministic fixture simulator

`gateway.fixture_simulator.simulate_read_item(item_id)` returns synthetic
fixture data without external access. It accepts only a built-in integer
from 1 through 1000; booleans, integer subclasses, other types, and
out-of-range values raise `FixtureValidationError` with the generic
message `Invalid item identifier`.

For example, item 42 returns exactly:
`{"item_id": 42, "label": "Synthetic item 42"}`.

Every valid identifier has a result. Repeated calls produce equal,
fresh dictionaries; modifying a result does not affect later calls.

The component performs no network, database, filesystem, environment,
clock, or random access. It has no persistent state or configurable
callbacks. It does not authenticate callers, authorize requests,
or accept policy decisions as execution permission.

The simulator is connected only through the explicit Python executor;
HTTP API requests do not invoke it.
Existing policy and audit results retain `not_executed`.
Synthetic data does not establish facts about real systems.

## Simulator execution event contract

`gateway.execution_audit` provides pure `build_execution_event` and
`validate_execution_event` functions. Both return fresh validated
dictionaries. Invalid records raise `ExecutionAuditValidationError`
with the generic message `Invalid execution event`.

Records contain exactly: schema_version, event_type, event_id,
execution_id, policy_event_id, occurred_at, principal_id, tool,
operation, status, and reason_code.

Schema version is the built-in integer 1. All other fields are built-in
strings. The fixed values are simulator_execution, reliability-reader,
fixture_store, and READ_ITEM for event type, principal, tool, and
operation respectively. All three identifiers must be canonical
lowercase version-4 UUIDs. Timestamps must be calendar-valid UTC values
in YYYY-MM-DDTHH:MM:SSZ format.

| Status | Reason code |
| --- | --- |
| started | SIMULATION_STARTED |
| completed | SIMULATION_COMPLETED |
| failed | SIMULATOR_ERROR |
| failed | INVALID_RESULT |

A started record describes intent before invocation, not proof that
invocation occurred. A completed record describes a validated result.
Failure reasons distinguish an invocation exception from invalid output.
These meanings must be enforced by future orchestration; validation alone
does not prove that the described activity occurred.

Each record has its own event_id. Records for one attempt share an
execution_id and reference its policy_event_id. The pure validator does
not check cross-record relationships, ordering, or the existence of the
referenced policy event. Absence of a terminal record leaves the outcome
unresolved from audit evidence alone.

The functions generate no identifiers or timestamps, perform no external
access, and maintain no state. Records exclude credentials, raw requests,
item identifiers, output payloads, and exception details.

This contract does not authenticate, authorize, persist records, or
enable dispatch. Existing policy audit receivers and the database table
do not accept these events. Existing policy and API behavior remain
non-executing.

## Execution event text receiver

`gateway.execution_audit_receiver.create_execution_audit_receiver(stream)`
returns a synchronous receiver for execution events. The caller owns
the text stream; the receiver never closes it.

Each call validates the event and serializes a fresh validated copy
before writing. Output is one compact JSON line with sorted keys and a
trailing newline. Success requires a complete write, a built-in integer
write count matching the line length, and a successful flush.
The receiver returns None on success.

Invalid events and serialization failures produce no output.
Write, incomplete-write, and flush failures propagate to the caller.
There are no automatic retries or fallback outputs. A failure can leave
partial or complete content; the receiver does not roll back output.

A per-receiver lock keeps each write-and-flush sequence together.
It does not coordinate separate receivers or processes.

Successful delivery does not prove durable storage, authenticity,
or actual simulator execution. This receiver alone does not satisfy a
durable-audit requirement. It does not check cross-record relationships,
deduplicate events, prevent replay, or enable gateway dispatch.

Existing policy audit receivers and database behavior remain unchanged.

## Simulator result validation

`gateway.fixture_result.validate_fixture_result(result, *,
expected_item_id=...)` checks a returned answer against an expected
synthetic item and returns a fresh `FixtureResult` dictionary.

The expected identifier must be a built-in integer from 1 through 1000.
The result must be a built-in dictionary containing exactly item_id and
label. Its item_id must be a built-in integer equal to the expected
identifier. Its label must be a built-in string equal to
`Synthetic item <expected_item_id>`.
Booleans and subclasses of the required built-in types are rejected.

Invalid expected identifiers or returned answers raise
`FixtureResultValidationError` with the generic message
`Invalid fixture result`. Returned data is not included in the message.
Inputs are not modified.

Acceptance establishes agreement with the expected synthetic answer.
It does not prove simulator invocation, authenticity, authorization,
audit persistence, or facts about real systems. Future dispatch must
derive the expected identifier from the exact policy-approved request.

The checker performs no external access, simulator invocation, or
persistent-state changes. Existing simulator behavior is unchanged;
the HTTP API remains non-executing.

## Request-binding validation

`gateway.request_binding` provides pure `build_request_binding` and
`validate_request_binding` functions. Both return fresh checked
dictionaries without modifying caller input.

Records contain exactly schema_version, policy_event_id, principal_id,
tool, operation, and item_id. The format version is the built-in integer
1. The policy-event identifier must be a canonical lowercase version-4
UUID. Supported context values are reliability-reader, fixture_store,
and READ_ITEM. The item identifier must be a built-in integer from
1 through 1000.

Text fields must be built-in strings. Booleans and subclasses of the
required built-in types are rejected. Missing fields, extra fields,
unsupported values, and invalid supplied types raise
`RequestBindingValidationError` with the generic message
`Invalid request binding`.

The builder accepts explicit keyword-only values and supplies the format
version. Missing or unexpected function arguments retain Python's normal
TypeError behavior.

Acceptance checks the record's structure and supported values. It does
not authenticate the caller, grant permission, verify the referenced
policy event, or prove execution. Returned dictionaries remain mutable;
this component does not implement the immutable request snapshot needed
by future dispatch.

No identifiers are generated, records persisted, or simulator calls
performed. Existing policy, API, and audit behavior remain unchanged.

## Fixed request snapshot

`gateway.request_snapshot.capture_request_snapshot(request, *,
principal_id=...)` captures supported request values in a frozen,
slotted `RequestSnapshot` dataclass.

The snapshot contains principal_id, tool, operation, and item_id.
Supported context values are reliability-reader, fixture_store, and
READ_ITEM. Text values must be built-in strings; item_id must be a
built-in integer from 1 through 1000. Direct construction validates
these rules too.

Capture requires a built-in request dictionary containing exactly tool,
operation, and arguments, with a built-in arguments dictionary containing
exactly item_id. Identity is supplied separately, never read from the
request body. Invalid supplied values raise
`RequestSnapshotValidationError` with the generic message
`Invalid request snapshot`.

The snapshot retains no caller dictionaries. Changing the original
request after capture cannot change its stored values. Ordinary field
assignment and deletion are blocked. `to_policy_request()` returns a
fresh policy-shaped dictionary with fresh nested arguments on every call.

Capture does not authenticate or authorize. Future integration must obtain
identity from trusted authentication context and evaluate policy using
the captured values. The snapshot's immutability does not establish
permission.

Frozen dataclasses prevent ordinary accidental modification, not
deliberate bypass by privileged Python code. Capture is not atomic
against concurrent input mutation; callers must keep input stable
during capture.

No storage, audit delivery, identifiers, or simulator invocation are
introduced. Existing policy behavior remains unchanged, and the HTTP API
remains non-executing.

## Request-binding database schema

Apply `migrations/002_request_bindings.sql` after migration 001 using the
migration administrator and the existing owner, writer, and reader roles.
The migration is transactional and intentionally not repeatable.

`gateway_audit.request_bindings` contains exactly schema_version,
policy_event_id, principal_id, tool, operation, and item_id.
All columns are non-null. Constraints require version 1, the supported
principal/tool/operation values, and an item identifier from 1 through 1000.

The policy_event_id primary key permits one binding per policy event.
An immediate, non-deferrable foreign key references the existing
policy_decisions event_id. Updates and deletions do not cascade.

The table is owned by gateway_audit_owner. PUBLIC receives no table
privileges. The writer receives INSERT and SELECT; the reader receives
SELECT only. Neither application role receives update, deletion,
table-management, or grant authority.

The foreign key establishes referenced-row existence, not an ALLOW
decision, authentic origin, or permission to execute. A later receiver
must validate the matching ALLOW decision and binding and persist both
in one transaction. This schema alone does not require paired insertion.
Existing policy-only writes remain supported.

PostgreSQL column types do not preserve original Python input types;
Python validation remains responsible for exact built-in-type checks.

The isolated acceptance runner applies all three migrations, runs all
three SQL acceptance suites, and verifies that all three tables are empty
after each suite rolls back. These SQL checks are separate from the
1568 Python tests.

No transactional receiver, execution-event storage, or gateway dispatch
is introduced by this schema change.

## PostgreSQL policy and binding receiver

`gateway.postgresql_binding_receiver.create_postgresql_binding_receiver(conninfo)`
returns a synchronous receiver accepting a policy event and request
binding as two arguments. Connection configuration must come from trusted
application setup.

Before connecting, the receiver validates fresh copies of both records.
It requires ALLOW / POLICY_ALLOW, matching policy-event identifiers,
and matching principals. Invalid supplied records or relationships raise
`BindingPairValidationError` with the generic message
`Invalid policy binding pair`.

Each call owns one connection and uses READ COMMITTED isolation,
synchronous_commit=on, a five-second connection timeout, a two-second
lock timeout, and a five-second statement timeout. These settings do not
establish a total wall-clock deadline.

Parameterized inserts save the policy event first and its binding second.
One transaction commits both together. Pre-commit insertion failures
trigger rollback and connection cleanup. Cleanup failures do not replace
the original delivery failure.

Both inserts are strict: existing identifiers fail even when their
contents match. There is no duplicate repair, overwrite, automatic retry,
or fallback. Existing policy-only records are not completed by this receiver.

Success returns None after commit acknowledgement and connection close.
A lost commit acknowledgement can leave both records saved despite a
reported failure. A close failure after commit also propagates.
Callers must withhold dispatch on failure and reconcile uncertain
outcomes separately; rollback cannot undo an already completed commit.

Real database tests cover visible committed pairs, duplicate rejection,
existing policy-only records, rollback after a denied second insert,
post-commit failures, and read-only credential rejection.

Persistence does not authenticate the principal, prove actual policy
evaluation, or grant execution permission. Future trusted orchestration
must derive the records from the same captured request and its internally
evaluated decision. No HTTP integration, simulator invocation, execution
outcome storage, or replay prevention is introduced.

## Execution-event database schema

Apply `migrations/003_execution_events.sql` after migrations 001 and 002
using the migration administrator and existing application roles.
The migration is transactional and intentionally not repeatable.

`gateway_audit.execution_events` stores the existing eleven-field
execution-event contract. Every field is non-null. Constraints enforce
format version 1, simulator_execution, supported principal/tool/operation
values, canonical lowercase version-4 UUIDs, calendar-valid UTC timestamps,
and the four permitted status/reason combinations.

The event_id primary key identifies each record. An immediate,
non-deferrable foreign key links policy_event_id to an existing request
binding. Updates and deletions do not cascade.

Two partial unique indexes permit at most one started record and at most
one terminal record per execution_id. Completed and failed outcomes share
the same terminal limit. One start and one terminal record may coexist.

The dedicated owner owns the table. Writer access is INSERT and SELECT;
reader access is SELECT only. PUBLIC receives no table privileges.
Neither application role may rewrite saved records, manage the table,
or delegate table privileges.

These constraints do not enforce ALLOW, start-before-outcome ordering,
matching policy references across an attempt, or timestamp ordering.
They do not prove actual invocation or prevent repeated execution.
A start without a terminal record leaves the outcome unresolved.

SQL acceptance covers table metadata, references, valid and invalid
records, nulls, duplicate identities, competing outcomes, and application
permissions. All fixtures are rolled back.

This change adds storage and tests only. No execution-event receiver,
simulator orchestration, or HTTP integration is introduced.
The HTTP API remains non-executing.

## PostgreSQL execution-event receiver

`gateway.postgresql_execution_receiver.create_postgresql_execution_receiver(conninfo)`
returns a synchronous receiver accepting one untrusted execution event.
Connection configuration must come from trusted application setup.

The receiver reuses execution-event validation before connecting.
Invalid events retain `ExecutionAuditValidationError`. Invalid connection
configuration raises ValueError with the generic message
`Invalid execution database configuration`.

Stored context must include a matching request binding and an
ALLOW / POLICY_ALLOW policy event. A start requires an empty attempt.
A terminal event requires exactly one matching start and a timestamp
equal to or later than that start. Invalid stored relationships raise
`ExecutionRelationshipError` with `Invalid execution relationship`.

Each call owns one connection and transaction. READ COMMITTED isolation,
synchronous commit, and the existing connection, lock, and statement
timeouts apply. These settings do not establish a total deadline.

Before relationship reads, a transaction-scoped advisory lock serializes
cooperating receiver calls for the same execution_id. Its key is derived
from `gateway.execution_events:` followed by the canonical execution_id.
Reads after lock acquisition observe previously committed changes.
Hash collisions can serialize unrelated attempts; full identifiers
remain the basis for all relationship checks.

Insertion is strict. Existing identifiers or attempt states are rejected
without repair, overwrite, automatic retry, or fallback.
Success returns None after commit acknowledgement and connection close.
Pre-commit failures trigger rollback and cleanup without replacing the
original failure. Commit acknowledgement or post-commit close failures
may report failure despite a saved event; reconcile before retrying.

Real PostgreSQL tests cover committed visibility, denied and missing
relationships, invalid attempt transitions, competing calls, lock timeout,
read-only credentials, and injected delivery failures.

Advisory locking is cooperative; direct SQL writers can bypass receiver
relationship checks. Database constraints remain additional safeguards.
Saved events do not prove authentication, actual policy evaluation,
simulator invocation, or permission to execute. No simulator orchestration,
HTTP integration, approval lifecycle, or replay prevention is introduced.
The HTTP API remains non-executing.

## Simulator execution-result contract

`gateway.execution_result` provides pure `build_execution_result` and
`validate_execution_result` functions. Both require a separately supplied
expected_item_id: a built-in integer from 1 through 1000.

Records contain exactly schema_version, status, reason_code,
invocation_status, policy_event_id, execution_id, and result.
The version is the built-in integer 1. Text fields are built-in strings.
Both identifiers must be canonical lowercase version-4 UUIDs.

| Status | Reason code | Invocation status |
| --- | --- | --- |
| denied | POLICY_DENIED | not_invoked |
| completed | SIMULATION_COMPLETED | invoked |
| failed | PRE_EXECUTION_AUDIT_ERROR | not_invoked |
| failed | SIMULATOR_ERROR | invoked |
| failed | INVALID_RESULT | invoked |
| failed | OUTCOME_AUDIT_ERROR | invoked |
| failed | EXECUTION_UNCERTAIN | unknown |

Only completed records contain an answer. Existing fixture-result
validation requires the exact synthetic answer for expected_item_id.
Every other outcome requires result=None. Returned outer dictionaries
and completed-result payload dictionaries are fresh; input is unchanged.

Invalid records or expected identifiers raise
`ExecutionResultValidationError` with `Invalid execution result`.
Fixture-result validation failures use that same generic error.
The keyword-only builder supplies the version and reuses validation.
Missing or unexpected function arguments retain Python TypeError behavior.

These are checked claims, not proof of activity. Future trusted
orchestration must establish invocation status and required audit
acknowledgements before reporting completion. A failure after invocation
must not be described as not_invoked. Unknown means invocation cannot be
established from available evidence.

Identifiers do not prove that saved records exist. The component does not
authenticate, authorize, evaluate policy, access storage, generate
identifiers, or invoke the simulator. It covers results for supported
captured requests and does not replace malformed-request API responses.
Process interruption may prevent any result from being returned.

This pure result component performs no orchestration or HTTP integration.
The HTTP API remains non-executing.

## Explicit synthetic simulator execution

`gateway.simulator_execution.create_simulator_executor(conninfo)` returns
a synchronous callable: `execute(request, *, principal_id=...)`.
Configuration must come from trusted application setup. Identity must come
from trusted authentication context; the executor does not authenticate
credentials. Factory creation opens no database connections.

Execution is limited to fixture_store / READ_ITEM and synthetic items
1 through 1000. No per-request human approval is required for this specific
synthetic read. This does not establish an approval lifecycle or authority
for real tools.

The executor captures one fixed request snapshot and evaluates policy
internally. Unsupported inputs retain RequestSnapshotValidationError.
The current snapshot accepts only the fixed supported caller, tool, and
operation; this component does not broaden those contracts.
Unexpected ordinary preparation failures raise SimulatorPreparationError
with `Simulator preparation failed`.

For ALLOW, it saves the decision and binding together, then saves a start
event. Both deliveries must acknowledge success before the fixed simulator
is called once. Its answer is validated against the captured item.
A terminal event must be acknowledged before returning a successful answer.
For DENY, it saves only the policy decision and never invokes the simulator.

Pre-invocation delivery failures return PRE_EXECUTION_AUDIT_ERROR with
not_invoked. Simulator exceptions and invalid answers produce corresponding
failed events. Terminal construction or delivery failures return
OUTCOME_AUDIT_ERROR with invoked and no answer. Backward clock movement can
cause terminal rejection; timestamps are not silently adjusted.
Existing policy audit records retain not_executed.

Identical explicit requests are separate attempts with fresh internal
identifiers and all gates repeated. There are no automatic retries,
duplicate repair, resumption, or fallback. This is not replay prevention,
idempotency, or exactly-once execution.

A failed delivery can leave committed records. Missing terminal evidence
does not prove that invocation was absent. BaseException interruptions
propagate and may leave an unresolved attempt without a returned result.
Manual reconciliation is required before considering recovery.
See [manual reconciliation](#manual-reconciliation-of-a-synthetic-attempt).
Database and storage configuration determine power-loss durability;
timeout settings do not establish a total execution deadline.

Unit tests cover ordering, stopping rules, snapshot independence,
preparation failures, and interruptions. Real PostgreSQL tests cover
pre-invocation record visibility, correlated outcomes, repeated requests,
duplicate rejection, read-only credentials, clock reversal, and injected
insertion, commit-acknowledgement, and close failures. Denial-path tests
use controlled internal policy substitution.

This is opt-in Python composition only. No HTTP execution route or startup
integration is introduced. Real tools and operations with external effects
remain outside this component's scope.

## Simulator diagnostic logging

The Python executor uses the standard logger
`gateway.simulator_execution` with the fixed message `simulator_execution`.
It installs no handlers, changes no logging levels, and chooses no logging
destination. Application logging configuration controls visibility and
formatting; default formatting may omit the structured fields.

The component supplies only gateway_stage, gateway_outcome, and optional
policy_event_id and execution_id fields. Identifiers are included only
when they are canonical lowercase version-4 UUID strings generated by
the internal execution path. Missing or invalid identifiers are omitted.

| Level | Stage | Outcome |
| --- | --- | --- |
| INFO | snapshot | rejected |
| INFO | denial_audit | denied |
| INFO | result | completed |
| WARNING | snapshot | preparation_failed |
| WARNING | policy_preparation | preparation_failed |
| WARNING | allow_preparation | preparation_failed |
| WARNING | denial_audit | audit_failed |
| WARNING | pair_audit | audit_failed |
| WARNING | start_audit | audit_failed |
| WARNING | invocation | simulator_failed |
| WARNING | result_validation | invalid_result |
| WARNING | terminal_audit | audit_failed |

Failures are diagnosed at their existing handling boundaries. A simulator
failure followed by terminal-recording failure produces both diagnostics.
Successful intermediate stages are not logged. Completion is logged only
after terminal acknowledgement and construction of the checked result.

Component-supplied diagnostic content excludes credentials, connection
strings, caller identity, item identifiers, requests, answers, exception
details, and tracebacks. Handlers and formatters remain trusted application
configuration; correlation identifiers should receive appropriate access
and retention controls.

Ordinary diagnostic preparation or emission failures are suppressed without
retry or fallback output from the helper. Existing audit gates, returned
results, exceptions, and simulator invocation rules remain unchanged.
BaseException interruptions are not suppressed and can stop processing.

Diagnostics supplement required PostgreSQL audit delivery. A log message
never substitutes for an audit acknowledgement, authorizes execution,
proves actual invocation, or establishes durable storage.
This change adds no log transport, storage, retention policy, endpoint,
or global logging configuration.

## Manual reconciliation of a synthetic attempt

This procedure inspects saved evidence for the fixed synthetic simulator.
It does not resume execution, repair records, recover a withheld answer,
or authorize a retry.

### Preserve available evidence

Keep the returned status, reason_code, invocation_status, policy_event_id,
execution_id, calling code version, and failure observation time.
Do not collect credentials, raw requests, or exception payloads.
A returned result does not independently establish saved database contents.

If either identifier is unavailable, record correlation as unresolved.
Do not guess an attempt from item numbers or nearby timestamps.
Preparation failure or interruption may provide no identifiers.

### Inspect through the approved reader connection

The responsible database engineer must identify the intended database
and provide trusted connection configuration for gateway_audit_reader.
Do not use writer or administrator credentials or put passwords in commands.

This connection command is a template. Replace APPROVED_READER_SERVICE
with an existing approved libpq service for the reader role. This runbook
does not provision that service or select an operational host.

```bash
# Open the approved reader connection without loading psql startup commands.
psql -X --set=ON_ERROR_STOP=1 --dbname='service=APPROVED_READER_SERVICE'
```

In that session, replace the three values below with the approved database
name and the exact identifiers from the attempt. The guard deliberately
raises an error if identity or identifier checks fail.
On any error, inspection is incomplete: ROLLBACK or disconnect.
Do not disable the guard to make a query succeed.

```sql
-- Supply the independently approved database name.
\set expected_database 'REPLACE_WITH_APPROVED_DATABASE_NAME'
-- Supply the exact policy-event identifier from this attempt.
\set policy_event_id 'REPLACE_WITH_POLICY_EVENT_ID'
-- Supply the exact execution identifier from this attempt.
\set execution_id 'REPLACE_WITH_EXECUTION_ID'

-- Inspect one consistent snapshot without changing saved data.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
-- Bound each lock wait and SQL statement.
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '5s';
-- End an abandoned transaction after thirty seconds of inactivity.
SET LOCAL idle_in_transaction_session_timeout = '30s';

-- Fail with division by zero unless all required checks pass.
SELECT 1 / CASE
    WHEN current_database() = :'expected_database'
        AND current_user = 'gateway_audit_reader'
        AND length(:'policy_event_id') = 36
        AND :'policy_event_id' ~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND length(:'execution_id') = 36
        AND :'execution_id' ~
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    THEN 1 ELSE 0 END AS inspection_guard;

-- Record the actual inspection context.
SELECT current_database() AS database_name, current_user AS reader_role,
       transaction_timestamp() AS inspection_started_at,
       current_setting('transaction_isolation') AS isolation,
       current_setting('transaction_read_only') AS read_only;

-- Inspect the exact policy decision.
SELECT schema_version, event_type, event_id, occurred_at, principal_id,
       decision, reason_code, policy_version, execution_status
FROM gateway_audit.policy_decisions
WHERE event_id = :'policy_event_id';

-- Inspect its associated request.
SELECT schema_version, policy_event_id, principal_id, tool, operation, item_id
FROM gateway_audit.request_bindings
WHERE policy_event_id = :'policy_event_id';

-- Check both identifiers so conflicting references remain visible.
SELECT schema_version, event_type, event_id, execution_id, policy_event_id,
       occurred_at, principal_id, tool, operation, status, reason_code
FROM gateway_audit.execution_events
WHERE execution_id = :'execution_id' OR policy_event_id = :'policy_event_id'
ORDER BY execution_id, status, event_id
LIMIT 101;

-- Close inspection without changing saved records.
ROLLBACK;
```

Expected context is the approved database, gateway_audit_reader,
repeatable read, and read_only=on. An error means inspection did not finish.
Keep the transaction short. Timeouts do not establish a total deadline.

Other attempts sharing the policy reference may appear in the event query.
Do not combine those rows with the target execution_id.
If 101 rows appear, treat the output as potentially truncated and unresolved.

### Interpret the snapshot conservatively

Check exact identifiers, matching principal/tool/operation, the required
ALLOW / POLICY_ALLOW decision, and matching start/terminal references.
Terminal time must be equal to or later than start time.
A policy-only DENY is a valid non-executing policy outcome.
Database constraints alone do not enforce every receiver relationship.

| Observed target records | Interpretation |
| --- | --- |
| No matching records | No matching evidence was visible; absence does not establish invocation outcome. |
| Policy only | A decision was recorded; no complete execution chain is visible. |
| ALLOW decision and binding, no start | The request link was saved; invocation is not established. |
| Start without terminal | Intent was saved; invocation and final outcome remain unresolved from these records alone. |
| Matching start and completed terminal | Records claim a validated answer; they do not independently prove execution or recover the answer payload. |
| Matching start and failed terminal | Records claim SIMULATOR_ERROR or INVALID_RESULT; no successful answer is established. |
| Missing links, conflicting references, terminal without start, or invalid ordering | Evidence conflicts with the receiver contract; preserve findings and escalate without repair. |

A still-running writer may save more records after this snapshot.
Inspection does not prove that the calling process stopped. Establish its
lifecycle separately with the application owner; do not terminate unrelated
processes.

A trusted PRE_EXECUTION_AUDIT_ERROR / not_invoked result says this executor
call stopped before invocation, even if pre-run records committed.
A trusted OUTCOME_AUDIT_ERROR / invoked result says invocation occurred.
A completed terminal can coexist with a failed returned result after lost
commit acknowledgement or close failure. Do not rewrite that failure as
success or release an answer the executor withheld.

Stored contents remain claims whose trust depends on the writing path and
database protection. Direct SQL writers can bypass receiver-only checks.
Inspection proves neither authenticity nor power-loss durability.

### Record findings without retrying

Record identifiers, the available returned result, code version, inspection
time, observed states, mismatches, and unresolved facts in the approved
engineering record. Restrict access to request metadata, including item
identifiers. Do not publish credentials or sensitive query output.

Do not INSERT, UPDATE, DELETE, fabricate terminal records, reuse identifiers,
resume the old attempt, or automatically retry.
An empty inspection result is not permission to rerun anything.

Escalate unresolved findings to the application and database engineers.
A separately authorized new synthetic request is a new attempt with fresh
internal identifiers and every gate repeated. It does not repair or resolve
the previous attempt. Real tools require a separate recovery design.

## Synthetic fixture-result provenance

`gateway.fixture_provenance` provides pure `build_fixture_provenance`
and `validate_fixture_provenance` functions.
Both require a supplied answer and a separately supplied expected_item_id.
They reuse existing fixture-result validation to require the exact
synthetic answer for a built-in integer item identifier from 1 through 1000.

The builder accepts
`build_fixture_provenance(result, *, expected_item_id, observed_at)`.
The validator accepts
`validate_fixture_provenance(provenance, *, result, expected_item_id)`.
Both return fresh metadata dictionaries without modifying inputs.

Records contain exactly schema_version, source_type, source_id,
source_version, observed_at, result_sha256, and permitted_use.
The supported version is the built-in integer 1.
All other fields must be built-in strings; subclasses are rejected.

Supported labels are synthetic_fixture, synthetic-fixture-v1, and
synthetic_demonstration_only. The source identifier must be exactly
`fixture_store:READ_ITEM:<expected_item_id>` without zero padding.

The supplied observation time must use calendar-valid
`YYYY-MM-DDTHH:MM:SSZ` text with whole UTC seconds and year 0001 through 9999.
The component does not read the clock, establish when retrieval occurred,
or enforce freshness.

The digest is lowercase SHA-256 over UTF-8 bytes of the freshly validated
answer serialized with sorted keys, compact separators, ensure_ascii=True,
and allow_nan=False. The validator recomputes it and requires exact agreement.
Dictionary insertion order does not change the fingerprint.

Invalid metadata, answers, expected identifiers, or observation times raise
`FixtureProvenanceValidationError` with `Invalid fixture provenance`.
Underlying fixture-validation and timestamp-parsing details are suppressed.
Missing or unexpected function arguments retain normal Python TypeError.

This is a standalone synthetic metadata contract, not authenticated
provenance. Source and version labels remain claims; the intended-use
label does not enforce authorization. A digest is not a signature.
Predictable synthetic answers and matching metadata can be recreated
without invoking the simulator.

Acceptance does not prove invocation, audit persistence, authentic origin,
permission, real-world truth, or resistance to deliberate forgery.
Item identity and fingerprints still require appropriate access controls.

No external access, clock reads, generated identifiers, simulator calls,
storage, or dependency changes are introduced.
Existing execution results, HTTP behavior, audit records, and authorization
gates are unchanged. No integration into those components is added.

## Synthetic execution evidence envelope

`gateway.execution_evidence` provides pure `build_execution_evidence`
and `validate_execution_evidence` functions. Both require a separately
supplied expected_item_id and reuse the existing execution-result and
fixture-provenance contracts.

An envelope contains exactly schema_version, execution_result, and
provenance. Its format version must be the built-in integer 1.
The envelope must be a built-in dictionary; booleans and integer or
dictionary subclasses are rejected where those exact types are required.

The nested execution_result retains its existing seven-field format
and all supported status/reason/invocation combinations.
Completed results require provenance matching their exact checked answer
and the same expected item. Failed and denied results require provenance=None
and retain the existing prohibition on answer payloads.

`build_execution_evidence(execution_result, *, expected_item_id, observed_at)`
builds provenance for completed results using the supplied observation time.
For failed or denied results, observed_at must be None; other supplied
values are rejected rather than silently discarded.

`validate_execution_evidence(evidence, *, expected_item_id)` validates the
complete package. It does not generate provenance to replace missing or
invalid supplied metadata.

Both functions return independent dictionaries: the outer envelope, nested
execution result, completed answer, and completed provenance are fresh.
Inputs are not modified.

Invalid envelopes, nested records, expected identifiers, or supplied
observation times raise `ExecutionEvidenceValidationError` with
`Invalid execution evidence`. Documented nested validation errors are
normalized without displayed underlying exception chaining.
Missing or unexpected function arguments retain normal Python TypeError.

The package checks agreement between provenance, answer contents, and the
expected item. It does not cryptographically bind provenance to an execution
attempt. Identical answers from different attempts may have identical
provenance. Execution identifiers and source labels remain checked claims.

Acceptance does not prove invocation, authentic origin, saved audit records,
permission, actual observation time, or freshness. Provenance does not
replace required audit acknowledgement.

This standalone component performs no execution, clock reads, identifier
generation, external access, or persistence. It changes no executor return
contract, HTTP behavior, audit gate, diagnostic behavior, or dependency.
Runtime provenance integration remains outside this slice.

## Opt-in simulator execution with evidence

`gateway.simulator_evidence.create_simulator_evidence_executor(conninfo)`
returns a synchronous callable:
`execute(request, *, principal_id=...) -> ExecutionEvidence`.
Configuration and caller identity must come from trusted application setup.
This component does not authenticate credentials.

Factory creation delegates configuration to the existing simulator executor
and opens no database connection. The original executor interface and
seven-field return contract remain unchanged.

Each call captures supported request values, then calls the existing
audited executor exactly once with fresh captured request dictionaries.
The existing executor retains responsibility for internal policy evaluation,
required database acknowledgements, and the fixed simulator invocation.
Packaging checks use the same captured expected item.

Completed results receive provenance through the existing evidence builder.
The observation timestamp is generated after the underlying executor returns
and its result passes validation. It describes this component's observation
of that returned answer, not invocation time, source retrieval time, terminal
audit time, or independently verified freshness.

Failed and denied results retain their existing outcome inside the envelope,
with no answer or provenance. These paths do not read the observation clock.

Unsupported input retains RequestSnapshotValidationError.
Unexpected ordinary capture failures raise the existing generic
SimulatorPreparationError before execution.
Underlying executor exceptions and BaseException interruptions propagate.

Ordinary failures while validating or packaging a returned result raise
`SimulatorEvidenceError` with `Simulator evidence preparation failed`.
The error exposes only policy_event_id, execution_id, and invocation_status
from a successfully validated returned result. If validation did not succeed,
the identifiers are None and invocation_status is unknown.
The component does not attach answer payloads, requests, credentials,
provenance, or underlying exceptions as public error attributes.
Displayed exception chaining is suppressed.

A packaging failure does not undo simulator invocation or committed audit
records. The component returns no fallback answer and performs no retry,
resumption, or repair. Use the correlation context for manual reconciliation;
do not interpret a packaging error as permission to repeat execution.
The existing diagnostic completion message describes underlying execution,
not successful preparation or delivery of this envelope.

Unit tests cover one-call composition, captured-value independence, all
unsuccessful outcomes, safe packaging errors, and interruptions.
Real PostgreSQL tests cover correlated completion, controlled policy denial,
simulator failures, invalid answers, read-only credentials, lost audit
acknowledgements, and packaging failures after committed completion.

Provenance remains unsigned synthetic metadata and is not persisted.
The envelope does not independently authenticate origin, verify freshness,
or cryptographically bind provenance to an attempt.
No HTTP route, startup wiring, new audit record, schema, dependency,
operational tool, or deployment is introduced.

## Engineering controls

Use isolated changes, independent validation, and pull-request review.
Keep secrets and local environment files outside version control.
Treat external inputs and model responses as untrusted.
