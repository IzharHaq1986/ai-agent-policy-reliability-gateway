# Proposed human-approval lifecycle

Status: design only. No approval lifecycle described here is implemented.
The words "must" and "requires" below specify future acceptance requirements.

This proposal prepares a separately selected, approval-required synthetic
execution path. It does not enable that path or authorize operational tools.
The existing synthetic read continues to require no per-request approval.
The HTTP API remains non-executing, and policy still returns ALLOW or DENY.
No APPROVAL_REQUIRED policy result, endpoint, schema, or credential is added.

See the [implemented architecture](architecture.md) for current behavior.

## Purpose and trust boundary

A future approval authorizes one exact request to proceed toward dispatch,
subject to every other gate. Approval cannot override DENY, expand tool
scope, authenticate the requester, or replace required audit acknowledgement.

Trusted application setup must authenticate requester and approver identities.
Identity strings in request bodies, model responses, retrieved documents,
or tool output cannot establish either identity or approval.
An approval identifier is a reference, not a bearer credential.

The current API's single credential maps to reliability-reader.
That mapping does not identify an independent human approver.
An approval-required path must remain disabled until suitable authentication
and approver authorization have been implemented and validated.

Approver authority must come from server-owned rules for the exact scope.
The requester must not approve their own request.
A model or agent identity cannot act as the human approver.
Approver authorization must be checked when recording approval and again
before consumption. Missing or uncertain authority blocks the transition.

## Exact request binding

Trusted code must create a new request identity and immutable snapshot before
seeking approval. Existing RequestSnapshot and RequestBinding contracts
illustrate captured-value consistency; they are not approval credentials.

The approval record must bind at least:

| Binding | Required meaning |
| --- | --- |
| Request identity | One server-created request instance, not just equal contents |
| Requester | Identity derived from trusted authentication |
| Tool and operation | Exact allowlisted adapter and operation |
| Arguments and scope | All validated values and applicable resource limits |
| Versions | Request schema, approval contract, and relevant policy/adapter versions |
| Approval identity | Separate server-created reference to the lifecycle record |
| Approver and authority | Authenticated human identity and applicable authority |
| Time bounds | Recorded decision time and fixed expiry |
| Consumption | Single execution identity and committed consumption state |

For the initial proposed synthetic scope, arguments include the exact item_id
and scope includes fixture_store / READ_ITEM with no additional operations.
Any future expansion requires a new reviewed contract.

The human must see an accurate, unambiguous representation of the exact
request, requester, scope, and expiry before approving.
Display text must safely represent untrusted content and must not hide changed
arguments behind summaries or model-generated descriptions.
A changed request requires a new request identity and new approval.

Two requests for item 42 are separate request instances.
Approval for one cannot authorize the other.
A content fingerprint may help detect differences, but is not authentication
and cannot replace the protected request record and identity comparison.

Version changes invalidate this proposal's approval binding for dispatch.
The initial design requires new approval rather than silently migrating it.
Policy must still be evaluated internally against the captured request.

## Lifecycle and time

The proposed lifecycle uses the following states.
These are lifecycle states, not additions to the current policy result.

| State | Meaning |
| --- | --- |
| pending | Captured request awaits an authorized human decision |
| approved | Human approval is recorded, but execution is not authorized yet |
| rejected | Human declined; terminal for this approval |
| expired | The approval window ended; terminal for this approval |
| revoked | An authorized actor withdrew pending or approved permission |
| consumed | One execution identity claimed the approval; never reusable |

Allowed transitions are pending to approved, rejected, expired, or revoked;
and approved to expired, revoked, or consumed.
Terminal states have no outgoing transitions.
A later request requires fresh identities, approval, and all gates.

Pending records must have a server-set expiry.
Approval must not extend that expiry.
Eligibility requires trusted_now < expires_at; equality means expired.
Expired pending or approved records are ineligible even if their saved state
has not yet been updated to expired.

A trusted server-side time source must govern expiry and transition ordering.
Client timestamps and model-supplied times cannot govern eligibility.
Invalid time, detected clock anomalies, or unavailable trusted time block use.
A clock correction must not revive expired, revoked, rejected, or consumed state.
Clock authority, anomaly detection, timestamp precision, and permitted approval
duration must be resolved before implementation; no production defaults exist.

## Consumption, concurrency, and dispatch

A future protected persistence transaction must serialize competing transitions
for the same approval and request, using an enforceable database mechanism.
A pure validator or process-local lock is insufficient across workers.

The consumption transaction must verify the exact immutable binding,
unexpired approved state, authenticated requester, approver authority,
and an internally obtained current policy ALLOW decision.
Relevant version and scope changes block consumption.

It must atomically record the transition to consumed, the bound execution
identity, and the required consumption audit evidence.
Exactly one concurrent consumption may commit for the approval.
All other contenders must receive a defined non-executing outcome.
The database constraints, isolation, privileges, and transaction boundaries
needed to enforce this remain implementation prerequisites.

Revocation and consumption must use the same serialization boundary.
If revocation commits first, consumption must fail.
If consumption commits first, revocation must report that it is too late
to revoke this approval; it must not imply cancellation of execution.
Any post-consumption stop request requires a separate cancellation design.

Consumption acknowledgement is required before invocation.
Loss of acknowledgement blocks invocation by that call, even if consumption
actually committed. The record remains unavailable for automatic reuse.

Immediately before invocation, trusted orchestration must recheck request
binding, policy, requester authorization, applicable versions, and expiry.
If any check fails or cannot be established, do not invoke.
A consumed approval remains consumed even if dispatch is blocked.
Trusted orchestration must define and bound the gap between this check and
invocation; approval does not reserve future policy authority indefinitely.

Existing required policy/binding and start-event acknowledgements remain
preconditions for invocation. A future integration must explicitly define
their ordering relative to approval consumption.
No existing audit gate may be removed, bypassed, or replaced by approval.

The future path must invoke only the fixed synthetic adapter selected by
trusted code. Caller-supplied decisions or approval records cannot select
another adapter or bypass dispatch gates.
Terminal recording and validated-result requirements still apply after
invocation. Approval does not justify releasing an answer withheld by
required audit failure.

## Failure and reconciliation

Consumed means permission was claimed, not that the simulator ran.
A crash after consumption but before invocation may leave no invocation.
A crash after invocation may leave an uncertain outcome.
A missing terminal record does not distinguish those cases.

Do not restore consumed state, fabricate terminal evidence, resume an old
attempt, or automatically retry after an uncertain outcome.
A separately authorized new request does not resolve the previous attempt.
Single-use consumption does not establish exactly-once tool execution.

Required lifecycle events must correlate approval, request, policy, and
execution identities where available, together with authenticated actors,
transition, relevant versions, and trusted timestamps.
Access and retention must protect identity and request metadata.
Credentials, raw exception payloads, and unnecessary answer content must
not enter lifecycle audit events.

Required state transitions and their audit evidence must commit together.
Diagnostic messages cannot substitute for those records.
An uncertain transaction outcome requires read-only reconciliation through
exact known identifiers and an authorized reader.
Missing identifiers remain unresolved; do not guess from items or times.

The existing [manual reconciliation guidance](../README.md#manual-reconciliation-of-a-synthetic-attempt)
does not inspect future approval records.
A separate approval-aware reader procedure and failure contract must be
implemented before an approval-required execution path is enabled.

## Future acceptance matrix

The following cases are required future tests, not executed evidence.

| Case | Required outcome |
| --- | --- |
| Missing or forged identity/approval claim | No approval authority or invocation |
| Requester attempts self-approval | Reject approval |
| Human lacks scope authority | Reject approval or consumption |
| Changed argument, requester, scope, or version | No consumption or invocation |
| Equal contents with another request identity | Prior approval unusable |
| Pending, rejected, expired, or revoked record | No consumption or invocation |
| Time equals expiry or trusted time is uncertain | No invocation |
| Two concurrent consumers | At most one committed consumption |
| Revocation commits before consumption | No consumption |
| Consumption commits before revocation | Report too late; no cancellation claim |
| Consumption or required audit acknowledgement missing | No invocation |
| Crash or lost acknowledgement after consumption | Preserve consumed/uncertain evidence; no retry |
| Policy becomes DENY before invocation | No invocation; do not restore approval |
| Start audit fails after consumption | No invocation; approval remains consumed |
| Invocation occurs but terminal recording fails | Withhold answer; preserve invocation context |
| Duplicate consumption request | No second invocation |
| Valid bound approval and every dispatch gate passes | One permitted synthetic invocation through that call |

## Decisions required before implementation

Resolve authenticated human identity, approver roles, revocation authority,
credential expiry/revocation, and separation-of-duties enforcement.
Resolve exact record schemas, version rules, protected database roles,
atomic transition/audit persistence, conflict semantics, and replay tests.
Resolve trusted time, expiry duration, clock anomalies, and dispatch timing.
Resolve audit retention, redaction, correlation access, manual reconciliation,
and interruption handling.

Future implementation must specify machine-readable outcomes and migration
compatibility without silently extending today's policy or executor contracts.
Each implementation slice requires its own bounded contract and acceptance.
This proposal supplies no authenticated approval, operational integration,
HTTP execution, replay prevention implementation, or production readiness.
