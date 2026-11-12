# Internal bounded command capture

`tests/support/nts_command_capture.py` is a Linux-only building block for a
future isolated NTS experiment controller. It has no executable entrypoint and
is not wired into the gateway, Docker runners, or time-source inspection.
Importing it does not run a command. The existing runner interfaces are unchanged.

Trusted controller code can call `capture(argv, deadline=..., budget=...)` with a
tuple of explicit arguments, one absolute `time.monotonic()` deadline, and a
shared `OutputBudget`. The controller must create the deadline and budget once
and reuse them across sequential queries. These objects are internal trusted
state, not caller-supplied evidence. They are not designed for concurrent use.

Each query uses the earlier of that deadline and two seconds after entry. An
invalid or expired deadline prevents launch. Captured stdout and stderr share a
32 KiB limit; all queries share at most 1 MiB of raw-output budget. Failed queries
also consume bytes and never receive a refund. Reads are limited as they occur;
one extra detection byte may be read, charged, and discarded to identify
oversize output. Oversize output is never returned as a successful partial result.
An exhausted shared budget prevents further launches.

Results preserve separate byte streams and local monotonic start/end readings.
The caller must decode and validate report fields independently. A zero exit
code is necessary for success. Nonzero exit, missing or inaccessible executable,
read failure, timeout, overflow, or cleanup failure produces a fixed `CaptureError`
code without printing command arguments or captured output. A handled interruption
still performs cleanup and propagates the interruption.

The command has no input stream and runs without a shell in a new process group.
Timeout and output overflow stop the owned group, including descendants remaining
in it. EOF on both streams does not establish process exit. Linux `waitid` observes
exit without reaping the leader, so cleanup can signal the original group before
its ID could be reused. Non-default SIGCHLD handling is rejected before launch;
the trusted caller must not run another child reaper against these processes. Cleanup sends SIGKILL, allows at most one second to reap
the direct child, and closes both pipes. Cleanup failure cannot return success.
Cleanup can extend beyond the query deadline; it never grants more collection time.

This is a trusted-command helper, not a sandbox for arbitrary programs. It
inherits the process environment, so the future controller must own environment
construction, the command allowlist, and endpoint validation. A descendant that
escapes the group, an uninterruptible kernel operation, or a killed controller
cannot be contained by this helper alone. Descendant zombies may require their
own parent or system reaper; the helper reaps only its direct child. It does not
stop a Docker daemon or a container merely by killing the Docker client.

## Fixed failure codes

- `INVALID_CAPTURE_INPUT`, `INVALID_DEADLINE`, `INVALID_OUTPUT_BUDGET`
- `OUTPUT_BUDGET_EXHAUSTED`, `OUTPUT_LIMIT`, `QUERY_TIMEOUT`
- `COMMAND_MISSING`, `COMMAND_UNAVAILABLE`, `COMMAND_FAILED`
- `CAPTURE_UNAVAILABLE`, `PROCESS_CLEANUP_FAILED`, `UNSAFE_CHILD_REAPER`

## Validation and remaining boundaries

Focused tests run harmless local Python commands. They exercise binary output
from both streams, exact and excessive sizes, shared-budget exhaustion, shared
deadlines, a real two-second query timeout, closed pipes with a still-running
process, and a descendant holding pipes after its parent exits. Failure tests
also cover unavailable commands, sanitized errors, interruptions, and cleanup
failure without permitting success.

The future live controller still needs its independent container-stop watchdog,
identity checks, bounded Docker orchestration, source/sample correlation,
serialized evidence-bundle limits, and independently verified resource removal.
The 1 MiB raw-output budget does not include JSON escaping or other metadata;
it is not proof of a bounded final evidence bundle. This helper establishes
neither time-source trust nor measurement age. Approval eligibility stays blocked.
