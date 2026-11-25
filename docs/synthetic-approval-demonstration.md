# Synthetic consumption-to-invocation proof

This test-only demonstration connects a disposable PostgreSQL consumption proof
to the existing fixed simulator. It does not enable human approval or add an
application execution path. Runtime human approval remains blocked.

## Everyday example

A practice ticket counter confirms that a ticket and its matching receipt have
been marked used before handing over one pretend item. Another clerk cannot use
the same ticket. If the receipt cannot be saved, the ticket was withdrawn, or
confirmation is lost, the affected attempt hands over nothing. Finding a saved
receipt later does not automatically restart the handover.

## What the tests exercise

The existing scenarios in
[`test_postgresql_approval_consumption_proof.py`](../tests/test_postgresql_approval_consumption_proof.py)
use the separate `approval_consumption_proof` schema and its dedicated writer and
reader roles. The fixture begins with synthetic approved records. It does not
model enrollment, a human decision, or the application's pending lifecycle.

The test helper reuses the existing claim-and-commit operation. Its successful
branch reads the committed consumed state and matching consumption event through
the separate reader role before calling `fixture_simulator.simulate_read_item(1)`.
A scenario-local wrapper records each actual simulator entry and still calls the
real fixed simulator. The result must match the existing synthetic item contract.
The wrapper is removed automatically at the end of each test.

| Scenario | Database evidence retained | Simulator observation |
| --- | --- | --- |
| Competing claims | PostgreSQL reports a lock wait; one consumption and matching event commit; the other claim changes zero rows | Exactly one call, after the winner's commit acknowledgement and independent read |
| Invalid consumption audit write | The named constraint rejects the event; rollback leaves the approval available with no event | Zero calls |
| Revocation commits first | The waiting claim changes zero rows; the approval stays revoked with no consumption event | Zero calls |
| Commit succeeds but acknowledgement is lost | A separate reader finds consumption and its event; a later reuse probe changes zero rows | Zero calls, including after reconciliation |

The lost-acknowledgement fault is injected immediately after a real commit. It
models the saved-state/failed-confirmation boundary; it does not reproduce a
network outage. The later reuse probe tests single-use persistence and is not an
automatic recovery procedure.

## Independent verification failures

Three additional parameter cases consume separate seeded fixture approvals with
normal commit acknowledgement. Each then injects a fault into the reader result
delivered to the existing verification helper. Before delivering the fault, the
injection performs the real independent query and checks the committed pair.
It does not alter the database records or replace the verification gate.

| Injected observation | Expected rejection | Evidence after the stopped attempt |
| --- | --- | --- |
| No matching rows reported | The existing matching-pair assertion fails | Zero simulator calls; actual consumption and matching event remain committed |
| Another execution reported in the audit reference | The existing matching-pair assertion fails | Zero simulator calls; the real saved execution references stay unchanged |
| Synthetic reader error | The existing helper propagates the error | Zero simulator calls; the committed pair remains available for investigation |

Each case checks that exactly one injected reader observation occurred. After
restoring the reader, reconciliation checks the real rows without invoking the
simulator. A new claim against the same approval changes zero rows and makes no
simulator call. The approval remains spent; reconciliation does not resume the
failed attempt. The existing successful contention scenario still provides the
one-call control after normal acknowledgement and matching independent evidence.

These injected observations do not reproduce replication lag, a network outage,
or identity compromise. The synthetic unavailable-reader error occurs after a
successful real query, at the observation boundary. Missing and mismatched
results are deliberately fabricated, not claims about PostgreSQL returning
uncommitted or stale rows. Inspect the injection and the retained real-reader
query separately when diagnosing a failure.

## Implemented start-only cases

Two additional disposable parameter cases now compose the existing policy,
binding, consumption, and start-persistence components. They stop after inspecting
start intent. They do not call the simulator executor or implement a runtime
approval controller. The earlier consumption-to-simulator tests above retain
their separate limited scope; they do not gain a start gate from these new tests.

Each case verifies the existing audit writer/reader roles separately from the
consumption proof roles. A fixed captured synthetic request is evaluated by the
real policy. The existing binding receiver must return normally after saving
the policy and request binding before consumption begins. The consumption
helper then commits normally, and the independent proof reader checks its pair
before the real execution receiver is called once with the matching start event.

| Case | Receiver delivery | Independently inspected evidence | Simulator calls |
| --- | --- | --- | --- |
| Normal start | Returns `None` after real commit and real close; acknowledged | Matching policy, exact item binding, consumed approval, consumption event, and one start; no terminal event | Zero |
| Post-commit close error | Real commit and real close finish, then the wrapper raises the fixed original error; unacknowledged | The same matching records remain saved, including the original start; no terminal event | Zero |

The scoped connection wrapper forwards isolation settings and SQL to the actual
PostgreSQL connection. The test checks exactly one connection attempt, a normal
commit return, and actual driver close state before injecting the error. It
retains the original exception object. This models an error reported at the
post-commit close boundary, not failure to release the connection or a real
network outage.

Independent inspection occurs after restoring the normal connection function.
It compares complete saved audit rows with the checked events and binding,
and checks the consumed pair through the separate proof reader. A later claim
changes zero rows. Repeated inspection preserves all records, the original
acknowledgement status, the error identity, and the one-entry start delivery
count; it does not retry delivery or resume dispatch. A scenario-local observer
of the real fixed simulator entry remains empty throughout both cases.

The occurrence times are fixed synthetic values. Reference correlation is owned
by the test; no database-enforced approval/request link or verified human
identity is added. These two cases cover only the normal start control and
post-commit close error from the broader contract below.

## Start-audit proof contract

The following contract retains requirements for the broader disposable proof.
The two cases above are implemented; other cases remain future acceptance
requirements. This contract does not enable runtime approval or invocation.

### Intent, not handover

After the practice ticket is marked used, the clerk records "preparing to hand
over." If saving or confirming that entry fails, the clerk hands over nothing.
Even a confirmed preparation entry records intent, not a completed handover.
The ticket stays used when preparation fails.

### Reuse and correlation boundary

Reuse the existing
[`create_postgresql_execution_receiver`](../src/gateway/postgresql_execution_receiver.py)
and [execution-event contract](../src/gateway/execution_audit.py). Do not add a
second production start-event store or weaken the receiver to accommodate the
consumption fixture. The receiver already checks an allowed policy decision,
its request binding, and the attempt's existing execution events. It does not
check approval consumption or establish human authority.

A future test composition must retain the following fixed scenario context:

| Reference or value | Required correlation |
| --- | --- |
| Synthetic approval ID | Select exactly one disposable approval row and its consumption event |
| Execution ID | Match the consumed approval, consumption event, and proposed start event |
| Policy-event ID | Match the acknowledged policy decision, request binding, and start event |
| Principal, tool, operation, item | Capture one fixed synthetic request; match policy and binding context, including the bound item |
| Start-event ID | Identify one original start-write attempt; do not replace it after uncertainty |
| Start status and reason | Use `started` and `SIMULATION_STARTED` under the existing event schema |
| Occurrence time | Use valid, explicitly synthetic fixture time; do not claim verified clock health |

The consumption fixture has no policy-event or protected-request binding column.
Its relationship to policy context must therefore be explicit in the test-owned
scenario, checked before start recording and in the independent observations.
This is test correlation, not a new database-enforced approval/request link or
complete protected-request binding. The start event does not carry an approval
ID or item ID; inspect its execution reference and linked policy binding rather
than inventing fields in the existing event contract.

Keep the proof writer/reader roles separate from the existing audit writer/reader
roles. Use their existing isolated acceptance connections and verify database
and role identities. Any future fixture setup must preserve least privilege;
it must not grant application roles lifecycle writes or broaden privileges
merely to combine these observations.

### Required future sequence

1. Capture fixed synthetic context and persist the policy ALLOW decision and
   matching request binding through the existing acknowledged binding path.
   Failure or missing acknowledgement stops before consumption and start writes.
2. Consume the selected disposable approval and save its consumption event in
   one transaction. Require normal commit acknowledgement for this attempt and
   independently verify the matching consumed pair. Failure stops before any
   call to the start receiver; a later inspection cannot resume the attempt.
3. Build the same attempt's start event using the existing contract and deliver
   it once through the existing execution receiver. Success requires the full
   receiver call to return normally with its documented `None` result, including
   connection close. A commit followed by a close error is not successful
   delivery acknowledgement for this attempt.
4. Independently inspect policy, binding, consumed approval, consumption event,
   and any start event through the appropriate read-only roles. This observation
   is additional evidence; it cannot replace a missing delivery acknowledgement.
5. End the start-only proof without invoking the simulator. Preserve zero actual
   simulator entries in every case, including the successful start-write control.
   Do not create a fabricated completed or failed simulator outcome event.

The complete approval-backed path still requires final checks of exact request
binding, current policy ALLOW, requester authorization, applicable versions, and
expiry, plus a justified bound between those checks and invocation. Human
eligibility, separation of duties, current scope authority, and healthy trusted
time remain separate prerequisites. This start-only proof supplies none of
those missing guarantees. A future passing final gate would still require
validated results and terminal audit handling after invocation.

### Future cases and required evidence

The following are contract requirements. Only the normal start and post-commit
close-error cases have been implemented by this slice; the other rows remain
proposed tests, not executed evidence:

| Case | Start evidence | Required handling |
| --- | --- | --- |
| Normally acknowledged start | Exactly one matching start independently visible with the expected policy binding and execution reference | Successful start-only control; no simulator call |
| Consumption or its verification fails | Start receiver call count is zero; no start for that scenario | Preserve any committed consumption; stop without invocation |
| Scenario execution references disagree | No start write for the mismatched scenario | Reject before receiver delivery; preserve saved evidence |
| Start transaction fails and rollback is confirmed | No start for the failed attempt; consumption and its event remain committed | Stop without invocation; do not restore approval availability |
| Start commits, then confirmation is lost | Original start independently visible; consumption and its event remain committed | Preserve original failure and identities; no invocation, retry, or automatic resume |
| Receiver close fails after commit | Original start may be saved; inspect through the separate reader | Treat delivery as failed; saved state cannot authorize continuation |

Use separate disposable scenario identities and record actual receiver entries
and actual simulator entries independently of returned status labels. Failed
start persistence must not roll back the previously committed consumption.
Every post-consumption case must retain the matching consumed pair and reject
a later reuse claim. Do not delete a saved start to manufacture rollback evidence
or substitute a new event or execution ID after an ambiguous outcome.

Injecting an error after a real start commit demonstrates saved evidence with
lost confirmation; it does not reproduce a network outage. Keep independent
inspection outside the injected connection boundary. Distinguish confirmed
rollback from uncertain persistence using real database observations, without
turning reconciliation into a recovery or dispatch procedure.

Remaining implementation requires its own recorded scope and real isolated
PostgreSQL acceptance, including full existing checks and resource cleanup.
Earlier consumption receipts do not verify the newly added start-only cases.

## Limits

These tests prove the explicitly written test sequence, not an integrated runtime
controller. The existing consumption and scope-revocation proofs retain their
assertions. A successful fixture consumption is not human authorization.

This fixture has no complete protected-request binding, verified human identity,
independent approver, or trusted-time evidence. It does not implement the full
policy/binding acknowledgement, consumption acknowledgement, start-audit
acknowledgement, final recheck, invocation, and terminal-audit sequence described
in the [human-approval design](human-approval-design.md).

No application code, migrations, database privileges, API routes, clock services,
or existing simulator executor behavior change. The ordinary policy endpoint
continues to report `not_executed`. No model or external tool is called.

## Independent verification and troubleshooting

Use the existing isolated acceptance runner from the repository root with the
repository's pinned development environment and Docker access:

```bash
python tests/support/run_postgresql_acceptance.py
```

The runner creates the disposable schema, supplies both proof-role connections,
runs the existing Python suite and SQL suites, and removes its database resources.
Use a fresh runner invocation for another acceptance attempt: these proof cases
commit their scenario records and are not repeatable against the same saved
fixture. Never point the proof at an application or production database.

Running pytest without the two proof DSNs skips these database cases. Such a run
does not establish this slice's acceptance. A real acceptance receipt must show
the existing authenticated PostgreSQL acceptance and cleanup results with no
Python test failures.

If the consumed state or event assertion fails, inspect the existing claim,
event, and commit operations together with the independent reader query. If the
call count is wrong, inspect the test invocation branch and its scenario-local
wrapper. If contention times out, inspect the existing PostgreSQL blocking check
and transaction cleanup. Do not repair a failure by removing assertions, retrying
unknown commits, weakening the lock evidence, or treating saved rows as permission
to resume. Preserve the failed receipt and diagnose the specific boundary first.
