# Independent disposable-container watchdog

`tests/support/nts_container_watchdog.py` is an opt-in host process for one
labelled disposable container. It does not launch a container, query a time
source, change a clock, or enable approval. Its caller is trusted test code,
not an HTTP client or an agent-supplied tool request.

## Arming and ownership

The caller creates a stopped container with label
`org.khyber.gateway.nts-experiment-owner` set to a fresh 32-character lowercase
hex token. It records the full 64-character container ID. The watchdog rejects
prefixes and arbitrary names and checks that exact ID, label, and created state
before arming. Labels are not credentials or a defense against an administrator
who controls the Docker daemon; they prevent accidental cross-run cleanup.

The caller launches the watchdog in a separate session, with no input stream and
its own output pipe, and supplies one absolute host monotonic deadline, the host
boot ID, and a new record path in a private caller-owned directory. The deadline
must be in the future and no more than 120 seconds after watchdog entry. No
retry or collector progress changes it. The caller must receive and validate an
explicit matching `armed` event before starting the container, and recheck that
the deadline has not expired. No acknowledgement means no permission to start.

The watchdog writes each event to its exclusively created, mode-0600 record and
flushes it to disk before notifying the caller. It never overwrites earlier
evidence. There are at most two events, each at most 2 KiB. A broken output pipe
does not disarm it; the final event remains in the record. The future live
controller still needs to supervise watchdog health after arming and reconcile
resources if the watchdog is killed or the host restarts.

## Deadline action and verification

At the deadline, the watchdog checks the boot ID and performs an exact-ID Docker
inventory query. If the container is present, it checks ownership again, then
force-removes that exact ID. Removal stops its workload and prevents a later
start of the same ID. It never stops the Docker daemon or uses broad prune or
name-based deletion. An already-absent container is reported separately; that is
not a claim that this watchdog removed it.

Every Docker query uses the existing bounded capture helper: a maximum of two
seconds, combined 32 KiB output, and a shared raw-output budget. The deadline
action has a separate ten-second cleanup allowance. This allows removal and
verification after collection time ends; it does not extend live collection.
A result later than ten seconds past the original deadline is failed even if
removal was verified. These are experiment bounds, not trusted-time thresholds.

A separate successful inventory query must confirm the ID is absent. An
inspection error, a terminated Docker client, or a removal command's exit code
alone cannot establish that. Unknown ownership, unavailable Docker, timeout,
failed removal, or failed verification returns failure and preserves a reason
code. If the action may have succeeded despite a failed response, the caller
must reconcile with read-only queries; it cannot treat the experiment as passed.

This is not a hard real-time guarantee under host failure, suspension, unavailable
Docker, or watchdog termination. The complete live controller still needs health,
identity, interruption, and timing-discontinuity handling. Watchdog success alone
is not authorization to run the live experiment.

## Synthetic Docker acceptance

`tests/support/run_nts_watchdog_acceptance.py --accept` builds the existing gateway
image with a unique test tag and runs three harmless sleep fixtures:

1. A running container while the collector deliberately blocks.
2. A container never started, followed by an attempted late start that must fail.
3. A running container whose collector closes its watchdog notification pipe.

The watchdog has an eight-second synthetic deadline. The collector blocks until
eleven seconds after that deadline, then checks the persisted events and issues
its own Docker absence query. Evidence must show completed removal before the
collector resumed. The longer observation wait includes the ten-second cleanup
allowance; it does not give the workload a new execution budget.

Each fixture has networking disabled, a non-root user, no capabilities, no
privilege escalation, a read-only root filesystem, no mounts or published ports,
one CPU, 128 MiB memory, 32 processes, no restart, and bounded Docker logging.
Build-time network access follows the existing gateway image build pattern.
There are no NTS exchanges and no use of the separately prepared chrony image.

The harness prints its unique image tag and fixture ownership reference before
creation so an unknown acknowledgement can be reconciled. It reconciles creation
failures through its unique ownership label,
resolves full IDs, verifies ownership before removal, and checks fixture absence.
It removes only its own image tag and temporary records. Bounded event evidence
and timing summaries are printed for preservation with the acceptance output.
Any unverified cleanup prevents an overall PASS.

On a host with direct Docker access, run with the validation Python environment.
On Hetzner, run the dedicated harness as a single sudo command after `sudo -v`:

    sudo -n -- "$HOME/gateway-approval-validation-venv/bin/python" tests/support/run_nts_watchdog_acceptance.py --accept

Do not substitute a nested sudo Docker wrapper for this harness. The query helper
must have permission to signal and reap its own Docker-client process groups.
Running the dedicated test with consistent host privileges provides that; it
does not add capabilities or root privileges inside the fixture. No host package
installation or service configuration is part of this command.

Local process tests use a filesystem Docker simulator to verify lifecycle and
failure handling. They are explicitly not Docker acceptance evidence. Real Docker
acceptance must pass separately before merge, along with all existing checks.
Every watchdog result retains unknown measurement age and blocked eligibility.
The live NTS controller, source/sample correlation, source accuracy assessment,
gateway freshness and uncertainty limits, and runtime approval remain open.

## Future health-supervision contract

Status: design only. The existing watchdog has no health channel or continuing
progress protocol. Its two-event record and current interfaces remain unchanged.
Live integration requires separately implemented and validated supervision.

### Responsibility and identity

A trusted host supervisor owns the experiment and monitors the watchdog from its
own control loop. Potentially blocking collection runs in a separate, bounded
worker; the supervisor must not wait for a collection command before checking
health. The independent watchdog retains its original deadline responsibility.
Neither process can guarantee action if the host stops scheduling it.

The trusted launcher records the host boot ID, collection reference, full owned
container ID and ownership token, original absolute monotonic deadline, and
watchdog process generation: PID plus kernel process-start reference. It verifies
that generation at startup and around health observations. PID existence, an old
armed event, or a health flag supplied by a caller is insufficient. Replacement,
restart, boot change, or inability to verify identity fails the experiment.

### Bounded progress protocol

Use a dedicated private local request/response channel, separate from stdout and
the persisted two-event record. The selected mechanism is a nonblocking Unix
sequenced-packet socket pair, handed by trusted code only to the supervisor and
its launched watchdog. Future implementation must validate platform support,
restrict descriptor inheritance, and close unrelated copies. Private descriptor
provenance and the verified process generation establish the local association;
identity fields asserted in a response do not establish it alone. This is not
protection against a privileged host administrator.

At most one challenge may be outstanding. Each challenge contains a new
unpredictable token and increasing sequence number. A response must echo both
and bind the boot, collection, container, ownership token, watchdog generation,
and unchanged original deadline. Each packet is limited to 2 KiB; socket buffers
and application state must also have explicit finite bounds. Reject oversized,
malformed, duplicate, unsolicited, mismatched, or out-of-order responses.

The watchdog generates a response in the same control loop that checks the
deadline and initiates removal. A separate responder thread cannot establish that
this loop is progressing. Deadline checks and due removal take priority over
health responses. No health operation may block, extend the deadline, or postpone
removal. A full queue, disconnected channel, or failed health write never disarms
the watchdog; it still attempts its original deadline action.

Each response carries its generation time from the same host monotonic clock
domain used by the supervisor. The supervisor records challenge-send and
response-receipt times, verifies their ordering against generation time, and
checks both response latency and generation age. The fresh challenge bounds the
response to this exchange; a queued response from an earlier exchange cannot
refresh health. Re-reading a packet does not reset its age. Interrupted
observation, clock-domain ambiguity, or unverifiable timing invalidates health.

This protocol demonstrates recent observed control-loop progress. It does not
prove uninterrupted progress between observations, future responsiveness, or
Docker's ability to perform removal.

### Explicit experiment limits

Before implementation is used, trusted experiment configuration must specify
challenge interval, response timeout, maximum generation age, supervisor reaction
limit, timing resolution allowance, finite channel capacity, and fallback cleanup
budget. Validate the limits together against bounded operations and the original
deadline. Missing, inconsistent, or unachievable limits prevent arming. Record the
selected values and their justification before a separately scoped experiment.
These are supervision limits, not production clock-health defaults.

Only a verified armed acknowledgement followed by a valid fresh health exchange
permits the future controller to start its already authorized container, while
the original deadline remains valid. During collection, supervise independently
of the worker and check current health before admitting further work and before
accepting a completed round. Expired health or a missed response fails the
experiment, stops admission, and cancels the owned collection worker through its
bounded process-cleanup path. Preserve diagnostic observations as failed-run
evidence; do not present them as a successful round.

Keep only bounded current protocol state and a bounded failure/summary record.
Do not append an unlimited heartbeat history to the existing two-event journal.
Required evidence that cannot fit its configured bound fails evidence production;
it must not be silently dropped to obtain a passing report.

### Failure, cleanup, and reconciliation

Detection reports a fixed failure reason; diagnosis has no execution authority.
The supervisor's separately recorded authorization permits bounded fallback
removal only of the already authorized exact owned container. It checks current
ownership using the trusted Docker endpoint before a destructive operation and
uses an independent successful inventory query to verify absence. Unverifiable
ownership blocks removal and requires reconciliation. Never stop the Docker
daemon, prune resources, or act on an unrelated container.

The supervisor coordinates cleanup, but the watchdog remains independently
armed for its original deadline. Their exact-ID removals may overlap. A command
error proves neither presence nor absence: use bounded read-only inventory to
resolve it. An already-absent result must not be attributed to whichever process
happened to observe it. Preserve both available outcomes without inventing a
single successful action. Unresolved state or cleanup failure remains explicit
and prevents overall success, even if useful measurements were collected.

Do not automatically restart the watchdog, substitute a new collection identity,
reopen the failed round, or extend its deadline. Successful eventual cleanup does
not reverse a health failure. Channel loss does not cancel the watchdog's
existing cleanup responsibility. After host restart, reconcile recorded resources
before any separately authorized experiment.

### Required future validation cases

| Observation | Required handling |
| --- | --- |
| Matching fresh responses and stable observed identity | Admit work only within the unchanged deadline and other gates |
| Only an old armed message is available | No start; readiness is not current health |
| Watchdog PID exists but its control loop stalls | Response timeout fails the experiment; bounded fallback cleanup |
| Watchdog exits or is replaced | Fail identity/health; no automatic replacement |
| Old or queued response arrives | Reject it; receipt cannot refresh generation age |
| Collector blocks | Supervisor continues health checks and bounded failure handling |
| Health transport fills or disconnects | Fail supervision; watchdog deadline action remains independent |
| Docker is unavailable | Record failed action or verification; keep resource state unresolved |
| Removal acknowledgement is ambiguous or actions overlap | Reconcile exact-ID state through independent inventory |
| Cleanup or required evidence preservation fails | Report the failure; no overall success |

These cases require implementation tests and a separately scoped synthetic proof.
They are not demonstrated by this document. Health supervision cannot establish
clock accuracy, authenticated time-source identity, measurement freshness,
acceptable uncertainty, or shutdown under host failure. Measurement age remains
unknown and approval eligibility remains blocked. Public examples and tests must
use synthetic references; operational receipts and planning stay private.
