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

## Limits

These tests prove the explicitly written test sequence, not an integrated runtime
controller. The other existing consumption and scope-revocation proofs remain
unchanged. A successful fixture consumption is not human authorization.

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
