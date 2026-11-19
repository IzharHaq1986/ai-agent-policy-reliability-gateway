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

### Isolated protocol building block

`tests/support/nts_health_protocol.py` implements only bounded version-1 JSON
messages and a single-owner exchange state machine. Times are explicit integer
host-monotonic nanoseconds; positive response-timeout and generation-age limits
have no defaults. Duration limits are inclusive; the original absolute deadline
is exclusive. Sequence numbers increase within the session. A trusted injected
token factory must produce unpredictable 256-bit lowercase hexadecimal tokens;
format checking and adjacent-token reuse detection do not prove randomness.

`Session.challenge` records one outstanding challenge. `respond` constructs a
matching response using supplied lifetime references and generation time.
`Session.accept` checks exact schema, types, references, challenge, ordering,
response latency, and generation age. `Session.check` allows a future supervisor
to detect an expired outstanding exchange without receiving a reply. Exchange
errors permanently fail that session. Successful validation returns no execution
permission and no continuing health lease.

The helper performs no I/O, clock reads, process verification, transport setup,
arming, or cleanup. Its caller must establish trusted identity and clock-domain
provenance, schedule independent checks, enforce all other supervision limits,
and handle disconnects or interrupted observation. A response builder can be
called by any code; only future protected transport integration can establish
that the expected watchdog actually generated it in its deadline loop. These
synthetic tests do not demonstrate supervision under a blocked collector or
real watchdog/Docker failure. Live integration and its proofs remain pending;
measurement age remains unknown and approval eligibility blocked.

### Isolated private packet transport

`tests/support/nts_health_transport.py` adds a Linux-only transport building
block. `create_pair` returns two owned, unnamed Unix sequenced-packet endpoints,
created with nonblocking and close-on-exec flags and verified before return.
There is no listener, filesystem socket path, network port, or process-handoff
API. Checked adoption is restricted to trusted bootstrap code as described
below; the existing watchdog does not use it.

Trusted code supplies `buffer_bytes` and `effective_buffer_limit` explicitly.
Requests range from 4 KiB to 1 MiB; the accepted ceiling must be at least twice
the request and at most 2 MiB. The helper reads back both endpoints' send and
receive settings and requires each to be between the request and ceiling.
Linux can double or clamp requested values; a readback outside the accepted
range fails setup. These are transport resource bounds, not clock-health limits.
For Unix sockets, SO_SNDBUF affects flow control but SO_RCVBUF does not. Bounding
both senders limits their queues; receive-setting readback alone cannot prove
queue capacity. No exact queued-message count or memory-allocation total is
inferred from these byte settings.

Each `send` or `receive` makes one nonblocking socket-operation attempt, without
an application readiness wait, retry loop, remainder send, or application queue.
`WouldBlock` reports temporary lack of progress and leaves the endpoint open.
The future supervisor must apply the existing deadline and protocol failure
rules; it must not busy-retry, extend a deadline, or treat this as health evidence.
This is not a hard execution-time guarantee under host or kernel failure.

Packets must contain 1 through 2,048 bytes, reusing the protocol limit. Oversized
outgoing input fails before sending. Truncated incoming records, unexpected
flags, or ancillary-data truncation fail and close the receiving endpoint.
Receive uses a zero-sized ancillary buffer: Linux discards excess SCM_RIGHTS
references and closes them, so passed descriptors are not accepted into the
application. Synthetic tests check descriptor counts and rejection of both
rights and credential ancillary input. This behavior is why unsupported
platforms fail instead of falling back to another socket type.

A zero-length record and orderly EOF cannot be distinguished from the returned
empty bytes alone. Both produce `EMPTY_OR_CLOSED`, close the endpoint, and supply
no health evidence. Known peer-disconnect send errors have a separate reason;
other permanent operation errors also close the affected endpoint. Fixed errors
never echo packet contents. A `cleanup_complete` field records close failures;
an uncertain descriptor close is not retried. The owner must close the other
endpoint too, including on setup, protocol, or orchestration failure.

The synthetic tests exercise actual local sockets, bounded backpressure,
truncation, closure, descriptor cleanup, and one exchange using the isolated
protocol. They do not establish sender identity or watchdog progress. Non-
inheritability prevents implicit inheritance through exec, not duplication by
fork. Future trusted launch code must explicitly restrict handoff and close all
unrelated copies. Socket-pair peer credentials obtained before handoff must not
be presented as the later child process's identity. No watchdog launch,
supervisor loop, Docker action, or live collection is implemented here.
Measurement age remains unknown and approval eligibility remains blocked.

Transport semantics are grounded in the primary references:

- [Python 3.12 socket API](https://docs.python.org/3.12/library/socket.html)
- [Linux Unix-domain socket semantics](https://man7.org/linux/man-pages/man7/unix.7.html)
- [Linux socket buffer semantics](https://man7.org/linux/man-pages/man7/socket.7.html)

### Future restricted child-channel handoff

Status: design only. Neither the transport nor the watchdog currently implements
this handoff. The following requirements must be implemented and synthetically
validated before a launch-bound health-readiness claim is possible.

#### Trusted launch and endpoint ownership

The trusted supervisor selects a fixed absolute interpreter/executable, reviewed
child entrypoint, argument schema, controlled environment, and trusted working
directory. Check the reviewed code/configuration identity and expected privileges
before launch. No shell, PATH-based executable selection, caller-supplied module,
command, descriptor number, or environment override may select the child.
Python launch code must avoid preexec_fn and use explicit subprocess options.
This profile is trusted test infrastructure, not an agent tool execution path.

Create the private pair immediately before the restricted launch. Keep both ends
non-inheritable in the parent and use close_fds with pass_fds containing only the
child endpoint, in addition to explicitly configured standard streams. Standard
input is closed or DEVNULL; diagnostic outputs have separate bounded handling
and cannot stand in for health evidence. Do not redirect the health endpoint
through stdout. The supervisor keeps its end and closes its child-end copy after
handoff; the child closes unrelated copies and restores close-on-exec on its
owned endpoint immediately after entry. Endpoint adoption must be a separately
validated trusted bootstrap operation, not a general external-FD API.

The launcher must prevent unrelated concurrent forks from retaining either end.
The reviewed child must not duplicate or transfer its endpoint or replace itself
through another exec. It may launch only fixed bounded query helpers with
close_fds and no health endpoint in pass_fds; every such launch path must close
fork-created copies before helper execution. Non-inheritable flags alone do not
prevent fork copies. A check of the two intended processes cannot prove that no
third process holds a copy. Descriptor confinement therefore requires an
explicit trusted-code and launch-environment assumption, plus tests of all
selected launch paths. If that assumption cannot be justified, readiness remains
blocked.

#### Process lifetime and bootstrap sequence

1. Record boot, collection, owned-container identity, ownership token, and the
   original monotonic deadline before launch. Require explicit startup and
   cleanup budgets; no production defaults or replacement deadline are supplied.
2. Retain the direct-child launch handle under one trusted owner. Establish a
   Linux pidfd for that child before any reaping, with supported signal/wait
   semantics and no competing waiter or automatic child reaper. If the platform
   or environment cannot preserve that association, fail startup. Never attach
   a pidfd to an arbitrary PID supplied in a message.
3. Observe boot, child PID and kernel start ticks, expected command/code profile,
   and effective privileges through bounded trusted local inspection. Reject
   exit, inaccessible evidence, mismatches, or inconsistent observations. Bracket
   bootstrap and health exchanges with the retained lifetime/exit checks.
4. Supply immutable collection context through a bounded, separately versioned
   bootstrap message on the inherited endpoint. The isolated codec below covers
   packet syntax only; delivery and adoption checks still require separate
   implementation. Do not reinterpret bootstrap as a health reply. The child checks its own lifetime against that context. Only after
   trusted lifetime observations agree may either side initialize the existing
   health protocol for that exact attempt.
5. Require successful restricted handoff, observed expected lifetime, a matching
   watchdog armed event, and a fresh challenge/response before the future
   supervisor reports launch-bound readiness. Every other policy, isolation,
   time-budget, and authorization gate must still pass before container start.

A pidfd is a lifetime/exit reference and permits targeted signaling; it does not
attest executable contents or identify the sender of a socket packet. PID and
start ticks are observation references, not cryptographic identities. Matching
observations do not prove uninterrupted continuity or detect every same-process
exec. Readiness depends on the reviewed no-replacement profile and the stated
host trust assumptions; never report executable attestation from these checks.

Initial process creation can itself block beyond an application timeout on some
platforms. Startup time counts against the existing absolute deadline, and
supervision must remain independent of potentially blocked launch or collection.
A late acknowledgement cannot restore readiness. Record a failed or unresolved
attempt if the deadline passes; do not claim a hard launch-time bound under host
or kernel failure. No container may start while handoff is incomplete.

#### What establishes the association

| Evidence | Supported conclusion | Unsupported conclusion |
| --- | --- | --- |
| Trusted creation and restricted endpoint handoff | Local channel association under descriptor-confinement assumptions | No other process can ever obtain a copy |
| Retained direct-child handle, pidfd, and matching local observations | Observed expected child lifetime and exit state | Every reply was sent by that process |
| Fresh matching protocol exchange | Valid response to the current challenge on that channel | Independent sender identity or future responsiveness |
| Creation-time socket peer credentials | Credentials recorded when the connection/pair was established | Identity of a later inheriting child |

The selected minimal design combines controlled handoff with retained lifetime
checks. It does not introduce per-message kernel sender verification. The current
transport rejects ancillary input, including credential messages. Enabling
SO_PASSCRED/SCM_CREDENTIALS would require a separately scoped transport/schema
change and proof; do not silently enable it, ignore rejection, or add a permissive
fallback. If the intended threat model requires independent per-message sender
verification, this design is insufficient and readiness must remain blocked
until that requirement is implemented and validated.

Untrusted same-user code capable of inspecting, controlling, or acquiring another
process's descriptors is outside the minimal descriptor-confinement assumption.
A privileged administrator can also replace code or manipulate processes. Record
this boundary explicitly; matching UID, a hidden descriptor number, private
message fields, or an unpredictable challenge is not isolation from those
actors. No new service or privilege escalation is authorized by this design.

#### Failure, cleanup, and required proofs

The launch owner retains the child handle and resource references even if the
caller loses an acknowledgement. Reconcile through that retained ownership and
bounded read-only state checks. If no trustworthy child association is available,
record unresolved launch/cleanup rather than searching names or signaling a
possibly reused numeric PID. Do not automatically launch another child, replace
the collection reference, or extend the deadline.

Process cleanup is separately authorized and restricted to the verified owned
child, preferably through its retained pidfd, followed by bounded exit/reaping
verification and owned-descriptor closure. No broad process-name kill, unrelated
process group, Docker-daemon action, or unverified descendant cleanup is allowed.
A signal acknowledgement alone is not proof of exit. Keep cleanup uncertainty
explicit; cleanup success cannot turn failed readiness into a successful run.
Do not terminate the remaining armed watchdog while its container may still need
deadline enforcement: first reconcile/remove the exact owned container under the
existing fallback authorization and independently verify absence, or preserve
the guard's deadline responsibility and escalate unresolved cleanup.

| Future synthetic case | Required handling |
| --- | --- |
| Expected child receives only its endpoint and replies in time | Verify all lifetime and readiness gates; record only their limited meaning |
| Launch fails or bootstrap is malformed/incomplete | No readiness; close owned endpoints and reconcile the child |
| An unrelated descriptor or endpoint copy survives launch | Fail the tested confinement invariant; no readiness claim |
| A stale reply or old armed event is replayed | Reject it; do not refresh health |
| Child exits, PID is reused, or replacement is observed | Reject the lifetime association; never follow the replacement |
| Inspection is interrupted or permission is denied | Treat evidence as unavailable and fail readiness |
| Startup acknowledgement is lost or arrives after deadline | Reconcile original references; no retry launch or late start |
| Close, signal, exit verification, or cleanup is uncertain | Record the unresolved outcome; no overall success |

These are future acceptance requirements, not completed proofs. The existing
workload-identity test concerns a container process, not this host-child launch
path. The current local socket tests do not demonstrate descriptor confinement
across launch. Watchdog integration, independent supervision, and their failure
proofs remain open. No live collection is enabled; measurement age remains
unknown and approval eligibility remains blocked.

Primary references for the proposed launch/lifetime rules:

- [Python 3.12 subprocess inheritance and launch behavior](https://docs.python.org/3.12/library/subprocess.html)
- [Linux Unix socket credentials and descriptor passing](https://man7.org/linux/man-pages/man7/unix.7.html)
- [Linux pidfd lifetime and reaping conditions](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)
- [Linux process start-time field](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)

### Synthetic direct-child handoff proof

`tests/support/run_nts_child_handoff_acceptance.py --accept 5 2` runs two
harmless fixed local fixtures with explicit five-second startup and two-second
cleanup budgets per case. It requires Linux pidfd support and the validation
Python environment. No sudo, Docker, network exchange, or clock change is needed.
Existing CI runs the command and strict-checks both support files. Focused
failure tests are in `tests/test_nts_child_handoff.py`.

Both cases now use bounded transport creation and checked adoption. The parent
creates the pair with an explicit 8 KiB buffer request and 16 KiB accepted
kernel-readback ceiling. It records the child endpoint's device and inode before
launch. The fixed absolute interpreter/fixture, isolated Python mode, controlled
environment/working directory, close_fds, and one pass_fds entry remain required.
Standard streams go to DEVNULL. Expected identity and resource bounds travel
only through the fixed fixture arguments; this is not a general trusted-bootstrap
message protocol, and no expected identity comes from a reply.

Isolated Python excludes the script directory from imports. The fixed fixture
therefore temporarily adds only its own reviewed absolute support directory to
load the protocol and transport modules, then restores the prior search path.
No argument or environment variable selects that directory. The interpreter,
fixture, and support files must remain reviewed and unchanged; this is not
executable attestation or a defense against host tampering.

The child first checks that the supervisor endpoint and deliberately inheritable
unrelated marker were excluded. It then validates an owned duplicate through
adopt_inherited and explicitly closes its inherited original exactly once.
Adoption does not close that original for it. Descriptor numbers can be reused:
the new owned duplicate may occupy a slot that was excluded earlier. Numbers
alone therefore cannot identify objects or prove universal confinement.

The version-2 bounded fixture report includes the owned-copy locator and an
original-release assertion. Version 1 cannot satisfy the checked-copy proof.
The parent validates locator type/range and rejects the original descriptor as
a supposed duplicate. Within its retained child only, it independently checks
the locator's kernel socket type, device/inode, close-on-exec and nonblocking
flags against pre-launch evidence. It separately checks absence of the original
slot and brackets these observations with lifetime/exit checks. Inaccessible
evidence fails; a child assertion alone cannot establish original release.
These are point observations under the fixed no-replacement/no-new-work profile,
not proof of uninterrupted stability or absence of every possible copy.

All normal send/receive operations now use the existing bounded Endpoint API.
Private descriptor access is restricted to this synthetic proof's pass_fds,
read-only readiness polling, and flag inspection. It is not a general endpoint
export API. Malformed, oversized, ancillary, or unexpected packets cannot fall
back to a permissive raw receiver. The fixed child accepts only one harmless
exit instruction and does not execute supplied commands.

The first case closes the parent's spare child-end copy. The child closes its
own inherited original after adoption, communicates using the validated copy,
and exits. The parent observes exit independently through its retained pidfd
before reaping. It then combines kernel hangup flags with the transport's
EMPTY_OR_CLOSED rejection to confirm this controlled profile's closure. Empty
bytes or a transport error alone are insufficient evidence.

The second case deliberately retains the parent's child-end copy. After child
exit, the pidfd still reports exit while receive reports would-block. Only after
closing the parent's spare copy does closure become observable. This retained
copy is an intentional negative fixture, never an allowed readiness setup.
Neither case relies on socket closure to infer the child's lifetime.

Startup covers preparation, launch, inspection, adoption, reporting, and exit
observation under one original deadline. If channel closure arrives before
final exit status, failed-start reconciliation observes only the retained pidfd
within that same deadline. It cannot reopen the channel, accept a late report,
resend an instruction, or renew readiness. Fixture exit status 6 preserves
unconfirmed original/duplicate release as an explicit failed attempt. Eventual
process cleanup cannot turn that failed attempt into success.

Initial process creation and kernel scheduling are not guaranteed interruptible.
The child also has a bounded lifetime. Separate cleanup signals only the retained
owned child and verifies exit/reaping, using the pidfd when available. Failed
pidfd setup requires the still-unreaped direct-child association before using
its launch handle. No name-based kill, replacement launch, or deadline reset is
allowed. A missing launch handle leaves child cleanup unverified; closing local
descriptors cannot establish that no child was created. Owned original and
adopted references have distinct cleanup paths; uncertain numeric closes are
not retried. Unverified cleanup prevents overall success.

The harness emits bounded private JSON records, including checked-adoption use
and the independently observed original absence, followed by PASS only after
both cases and cleanup pass. Public examples remain synthetic; operational
receipts must not enter Git. Failure tests preserve the earlier launch, lifetime,
timeout, inheritance, malformed-report, and cleanup cases. They also reject
wrong expected identity/resource settings, invalid owned-copy locators, a false
original-release assertion with the original actually retained, and lost
confirmation after the original really closed.

For independent troubleshooting:

| Failure | Inspect and independently check | Correction boundary |
| --- | --- | --- |
| Unsafe ownership or unavailable pidfd | Check a fresh single-owner process and Linux signal/wait support | Restore prerequisites; do not skip lifetime checks |
| Child exits before its report | Compare the fixed argv and adoption inputs with captured pre-launch identity | Correct the observed mismatch without accepting an old report |
| Invalid owned locator or endpoint mismatch | Compare the retained child's kernel device/inode and flags | Never follow a child-selected PID or trust the locator alone |
| Original remains open or its release is uncertain | Inspect the child's explicit source-close path and parent absence gate | No blind close retry or readiness claim |
| Missing/late report or pending exit status | Check the original deadline and retained pidfd evidence | No replacement budget or permissive receive fallback |
| Cleanup uncertainty | Reconcile only the recorded owned child and descriptor state | Do not infer exit from a signal or reuse numeric references blindly |

Purpose comments identify the child's adoption/ownership transitions, the
parent's independent observations, and the focused failure tests. These files
and the command above support verification without AI. Diagnose the first
observed divergence before changing code or weakening a check.

This remains a fixed synthetic launch-path proof under explicit reviewed-code,
controlled-host, single Python-thread, and no competing/native reaper assumptions.
It does not establish per-message sender authentication, universal descriptor
confinement, executable attestation, or actual watchdog readiness. Real watchdog
handoff, general trusted-bootstrap delivery, health-loop integration, independent
supervision, and live failure proofs remain open. Live NTS collection remains
unstarted; measurement age remains unknown and approval eligibility blocked.

### Checked inherited-endpoint adoption

`nts_health_transport.adopt_inherited` adds an internal trusted-bootstrap
operation to the existing transport. It receives a borrowed descriptor, expected
kernel device/inode identity, and explicit existing buffer bounds. Expected
identity must come from trusted launch evidence; obtaining matching numbers from
an untrusted reply would not establish provenance. This slice performs no child
launch, bootstrap-message exchange, or actual watchdog integration.

Adoption duplicates the borrowed descriptor, then validates and owns only that
copy. It rejects non-sockets, wrong identities, unsupported socket families or
types, named or unconnected endpoints, blocking kernel mode, and existing buffer
readbacks outside the accepted bounds. A zero-time poll rejects observed peer
closure or errors without consuming queued data. It does not wait for readiness,
retry, resize buffers, or repair a blocking source. This is a point observation,
not a guarantee that the peer will remain present.

The duplicate's close-on-exec flag is set and verified independently of the
borrowed original's flag. Actual kernel nonblocking mode is checked before
wrapping; Python wrapper timeout bookkeeping is normalized to nonblocking only
after that check. Under the single-owner assumption, the shared kernel mode was
already correct and remains unchanged. This distinction matters because socket
duplicates share socket state even though their close ownership and exec flags
are separate. Creation and adoption reuse the same resource-bound checks.

Success returns an owned Endpoint using the existing packet limits, ancillary
rejection, backpressure handling, and close behavior. It leaves the borrowed
original open on both success and failure. The trusted caller must close its
original copy after successful adoption, before further fork/exec, handoff, or
readiness. Leaving that copy open can hide closure, as the preceding synthetic
proof demonstrates. An adoption result is not confirmation that the caller's
remaining descriptors were closed.

Failure closes only the acquired duplicate. Fixed errors distinguish invalid
references, unsafe bootstrap context, rejected observations, unavailable kernel
evidence, and unverified cleanup. An uncertain close is not retried. The
cleanup_complete field concerns only resources owned by adoption; it says
nothing about release of the caller's borrowed descriptor or other bootstrap
resources. Interrupted validation also releases the acquired copy.

This operation requires a controlled single-owner bootstrap with no concurrent
close/reuse, socket mutation, or descriptor transfer. It rejects multiple active
Python threads, but that check cannot exclude native threads, external same-user
interference, or privileged actors. These remain explicit trust assumptions,
not security properties inferred from an inode or descriptor number. Existing
creation/send/receive interfaces and watchdog behavior remain unchanged.

Focused transport tests use actual local descriptors and fault injection. They
check identity/type/name/mode/buffer rejection, closure and queued-data
preservation, duplicate/wrapper failures, and uncertain cleanup. They verify
that rejection preserves the source, that closing an adopted copy preserves the
source, and that an accepted copy still works after the original is closed.
The native unconnected-socket test requires permission to create a standalone
Unix packet socket. A sandbox that denies that setup cannot supply that test's
acceptance evidence; CI and Hetzner must still run it successfully. No skip or
weaker assertion replaces that gate.

For independent checks, inspect the adoption function and its matching tests in
`tests/test_nts_health_transport.py`. Compare the borrowed source's fstat identity,
kernel blocking flag, exec flag, and buffer readbacks before and after the call.
For unavailable evidence, inspect the reported operation and host permissions;
for cleanup uncertainty, reconcile only the owned copy without closing or
retrying the borrowed descriptor. Correct the cause rather than disabling the
identity, type, or resource checks.

The synthetic child proof above now uses checked adoption with fixed trusted
fixture context. Real watchdog handoff, a general bootstrap-delivery protocol,
sender verification, and health supervision remain separate work. Adoption
alone supplies no execution authority. Live collection remains unstarted,
measurement age remains unknown, and approval eligibility remains blocked.

The [Python descriptor documentation](https://docs.python.org/3.12/library/os.html#os.dup)
and [socket mode documentation](https://docs.python.org/3.12/library/socket.html#notes-on-socket-timeouts)
define the duplication and shared-mode semantics used here.

### One synthetic child health exchange

The fixed child proof now exercises the existing `Session` / `respond` protocol
through the checked private transport after the parent validates the adoption
report and kernel witnesses. This is a synthetic communication check, not actual
watchdog health supervision. The report remains a separate version-2 fixture
message; it cannot itself satisfy a health challenge.

Before launch the parent creates a fresh collection UUID and captures the boot
identity. Those two fixed context fields follow the existing adoption arguments
in the reviewed child's argv. The parent constructs the expected protocol lifetime
from its retained child PID, independently inspected start ticks, and original
monotonic deadline. The child constructs its matching lifetime from fixed launch
context and its own process references, not from the challenge's claimed identity.
All-zero container and owner references are explicit synthetic placeholders;
there is no container or real watchdog represented by this exchange. This is not
a general bootstrap-delivery API. No challenge token is supplied through argv or
written to the proof receipt.

The parent issues exactly one unpredictable 256-bit challenge. The explicit
synthetic response and generation-age limits are both one second. These test
settings are not production defaults or NTP freshness thresholds. The receive
wait ends at the earlier of the response deadline and the original startup
deadline. An empty receive returns to bounded readiness waiting; a blocked send
fails without resend, another token, another child, or an extended budget.
Protocol rejection and observed peer loss also fail the attempt and enter the
existing bounded owned-child cleanup path.

The child answers only one challenge and then accepts only the fixed exit
instruction. The parent accepts a response only for its outstanding token,
sequence, expected lifetime, and protocol timing bounds. It rechecks point
process and boot identity before requesting exit. Extra queued replies invalidate
the existing after-exit closure proof; they cannot silently become evidence for a
second exchange. Both the normal handoff and retained-parent-copy cases still
require independently observed child exit, original-descriptor absence, closure
behavior, and confirmed resource cleanup. The successful receipt adds
`synthetic_health_exchange_accepted: true`, while retaining unknown measurement
age and blocked approval eligibility.

Run the existing opt-in proof and focused tests from the reviewed checkout:

```bash
python tests/support/run_nts_child_handoff_acceptance.py --accept 5 2
python -m pytest -q tests/test_nts_child_handoff.py
```

Inspect the parent `_health_exchange` function for challenge ownership and the
unchanged-budget wait. Inspect the fixed child's one-challenge branch for reply
construction and exit restrictions. Focused real-child tests inject malformed,
wrong-token, wrong-lifetime, future-generation, missing, duplicate, and closed-peer
responses. Parent-side tests exercise late acceptance, replay rejection, send
backpressure, and a temporary receive race. They retain actual child handles and
independently verify reaping; rejection is not itself proof of resource cleanup.

For `HEALTH_EXCHANGE_REJECTED`, compare protocol fields against parent-owned
context and inspect `Session.accept`; do not repair or accept the rejected reply.
For `HEALTH_RESPONSE_EXPIRED`, inspect elapsed time and scheduling against the
original bounds; do not reset the deadline. For transport or peer-exit failures,
inspect the endpoint operation and retained pidfd evidence. For unexpected packets
after exit, inspect whether the child emitted more than one response. Correct the
first observed divergence without weakening the identity, packet, deadline, or
cleanup checks. No fault permits a replacement child within the failed attempt.

A timely synthetic reply is neither per-message sender authentication nor proof
of future cleanup capability, continuous process integrity, authenticated NTS,
measurement freshness, or acceptable clock uncertainty. Real watchdog integration,
independent supervision, source-trust evidence, and live NTS collection remain
open. Measurement age remains unknown and approval eligibility remains blocked.

### Identity rejection checkpoints

An `IDENTITY_MISMATCH` still stops the synthetic attempt. Its exception now carries
one fixed checkpoint label in `identity_checkpoint` and an
`IDENTITY_CHECKPOINT=...` exception note. Pytest displays the note when showing the
exception or a failed expected-error comparison. The opt-in CLI adds
`identity_checkpoint` to an identity-failure record; other error records retain
their existing fields. No raw command arguments, process references, boot IDs,
report values, or environment contents are added to these diagnostics.

| Checkpoint | Existing rejection site to inspect |
| --- | --- |
| `COMMAND_SNAPSHOT` | The bounded kernel command bytes disagree with the expected fixed argv or exceed the read limit |
| `REPORT_START_REFERENCE` | The independent process-start observations around the fixture report disagree |
| `REPORT_FIELDS` | A required report value or its exact type disagrees with the expected context |
| `REPORT_BOOT` | The boot observation at the report gate differs from the initial reference |
| `REPORT_CHILD_EXIT` | The retained child is observed exited at the report gate |
| `HEALTH_START_REFERENCE` | The post-reply process-start observation differs from the initial reference |
| `HEALTH_BOOT` | The post-reply boot observation differs from the initial reference |
| `HEALTH_CHILD_EXIT` | The retained child is observed exited after the reply |

Checkpoint labeling itself adds no identity retry, query, wait allowance,
permissive comparison, replacement launch, or recovery action. The startup
observation ordering below defines when the current lifetime and command checks
run. Existing high-level failure reasons, packet checks, deadlines, and
independent cleanup requirements remain in place.
Unavailable observations and other non-identity errors keep their existing
handling. Unverified cleanup still overrides an otherwise useful result.

For independent verification, run `tests/test_nts_child_handoff.py`. The real-child
fault tests cover all eight labels, preserve the high-level error reason, inspect
the fixed exception note, and independently check child reaping. The additional
checkpoint cases also verify one launch and descriptor-count restoration. They
mark report and reply boundaries rather than depending on scheduling delays.

A checkpoint identifies where rejection occurred, not why the observation
changed or whether it was a race, unavailable evidence, interference, or a
fixture defect. Preserve the original failing receipt and source identity. Use
the label to inspect the corresponding observation and its caller before making
a correction. Do not broaden accepted errors or retry into acceptance. A later
passing run alone does not establish that an unexplained earlier failure was
fixed. Live NTS collection remains unstarted; measurement age remains unknown
and approval eligibility remains blocked.

### Startup observation ordering

The synthetic harness must not infer publication of `/proc/PID/cmdline` merely
from acquisition of a `Popen` handle. Upstream Linux v6.8's
[`get_mm_cmdline`](https://github.com/torvalds/linux/blob/v6.8/fs/proc/base.c)
can return zero while startup has not yet initialized the environment bounds.
The [`begin_new_exec`](https://github.com/torvalds/linux/blob/v6.8/fs/exec.c)
path closes close-on-exec descriptors before the ELF loader subsequently
initializes argument/environment bounds in
[`create_elf_tables`](https://github.com/torvalds/linux/blob/v6.8/fs/binfmt_elf.c).
This supports a startup-publication timing explanation; it does not attest the
precise execution path of a distribution-patched kernel. The correction does
not infer trust from an empty result or from a later diagnostic reread.

The fixed synthetic sequence now works as follows:

1. Retain the direct child and pidfd, then read a bounded independent process-start
   reference before the report. Reject inaccessible, invalid, or dead-process
   evidence. `_start_reference` does not read or validate the command profile.
2. Receive the existing bounded fixture report under the unchanged original
   deadline. This is only a bootstrap-progress indication. Its PID, start ticks,
   descriptor locator, and release assertions remain untrusted.
3. Recheck the original deadline and perform the first full `_inspect`: read the
   independent start reference and compare the entire bounded command snapshot
   exactly against the parent's fixed launch argv. Require agreement with the
   pre-report start reference before examining the reported endpoint locator.
4. Perform the existing endpoint and original-descriptor-absence checks. Perform
   the second full `_inspect` after those observations and require the same
   pre-report reference. Preserve all report-field, boot, and exit checks.
5. Only then begin the existing synthetic health exchange. Keep the existing
   third full command/lifetime inspection, boot check, and exit check after the
   reply, before sending the fixed exit instruction.

Both full command checks before the challenge remain mandatory; they now bracket
endpoint observations after the report. The independent start witness still
brackets report receipt. Each full profile check reuses the same bounded stat
parser through `_start_reference`. The additional stat observation consumes the
original startup budget; it creates no new time allowance. No expected identity
is taken from a report, and no profile comparison is replaced by a child claim.

An empty, oversized, or different command snapshot at any of the three full
checks still fails immediately as `COMMAND_SNAPSHOT`. There is no retry, sleep,
replacement child, or loop waiting for a matching identity. Later diagnostic
observations never turn rejection into acceptance. Missing or invalid reports,
changed start references, unavailable evidence, and unconfirmed cleanup remain
blocking failures. This remains a point-observation proof under the existing
reviewed-code and controlled-host assumptions, not continuous process integrity.

For independent verification, inspect `run_case`, `_start_reference`, and
`_inspect` in the harness. Focused tests supply an empty command result if queried
before the real report; successful cases prove that no such query occurs and
that all three strict command checks remain. They record the start/report/profile/
endpoint/health order without sleeps. Separate cases inject empty, oversized, and
different command results into each full check, require no reread or relaunch,
and independently verify reaping and descriptor restoration. Additional tests
retain the pre-report lifetime binding and block progression without a valid
report. To repair a failure, inspect its recorded checkpoint and first divergent
observation; do not loosen the comparison or extend the deadline.

The child fixture, transport, protocol, real watchdog, HTTP boundary, and approval
paths are unchanged by this ordering correction. Live NTS collection remains
unstarted, measurement age unknown, and approval eligibility blocked.

## Future bootstrap context and acknowledgement contract

Status: packet syntax/equality and isolated supervisor-side and child-side
in-memory phases are implemented by the helpers below. Delivery, transport
dispatch, real-watchdog handoff, and independent supervision remain
design only. The existing watchdog CLI and two-event persisted record remain
unchanged. The current synthetic fixture still receives its fixed context through
reviewed argv; its version-2 report and one health exchange do not implement this
future context-delivery protocol.

In everyday terms, the supervisor prepares one job card for one worker and asks
for a receipt for that same card. The receipt means only that the worker checked
and adopted the card. It is not permission to begin work, proof that the watchdog
is armed, or evidence that its clock readings are trustworthy.

### Exact message schemas

A bootstrap packet is one complete UTF-8 JSON object, at most the existing
2,048-byte packet limit including whitespace. It has exactly the following ten
fields. No nested values, extra keys, duplicate keys, missing keys, BOM, invalid
UTF-8, type coercion, or truncated input is accepted. Writers produce compact,
sorted-key JSON; receivers compare validated field values and exact types, not
JSON key order. A boolean is never an integer. Integer fields are in the existing
signed-63-bit positive range, 1 through 9223372036854775807.

| Field | Context packet | Acknowledgement packet |
| --- | --- | --- |
| `version` | Integer `1`, the bootstrap schema version | Integer `1` |
| `kind` | Exact string `bootstrap_context` | Exact string `bootstrap_ack` |
| `token` | Fresh unpredictable 64-character lowercase hexadecimal bootstrap token | Exact value from the adopted context |
| `boot_id` | Lowercase UUID-shaped string using the existing Lifetime format | Exact value from the adopted context |
| `collection_id` | Lowercase UUID-shaped string using the existing Lifetime format | Exact value from the adopted context |
| `container_id` | Full 64-character lowercase hexadecimal container ID | Exact value from the adopted context |
| `owner` | 32-character lowercase hexadecimal ownership label token | Exact value from the adopted context |
| `watchdog_pid` | Positive integer retained-child PID | Exact value from the adopted context |
| `watchdog_start_ticks` | Positive integer kernel process-start reference | Exact value from the adopted context |
| `deadline_ns` | Positive integer absolute host-monotonic nanoseconds | Exact value from the adopted context |

The UUID-shaped fields have the exact existing `8-4-4-4-12` lowercase hexadecimal
shape; this contract adds no UUID-version interpretation. The seven lifetime
fields retain their existing health-protocol names and types. Bootstrap version
1 is a separate namespace from health version 1: dispatch requires the expected
phase and exact `kind`, not a version number alone. The acknowledgement echoes all
seven lifetime fields and bootstrap token without changing or deriving any of
them. Trusted parent code generates the unpredictable 256-bit token after the
independent launch checks and before freezing the context. Generation failure
blocks bootstrap; format checking alone does not prove unpredictability. Do not
reuse this token for a later health challenge. There is no bootstrap sequence
counter, replacement token, replacement deadline, or second context within the
same launched attempt. Exact token/context agreement, the fresh collection
reference, and retained launch association correlate the receipt to this context;
they are not message authentication or cryptographic proof of sender identity.

For example, these are synthetic schema illustrations, not launch instructions
or evidence of an existing container or valid deadline:

```json
{"boot_id":"11111111-1111-4111-8111-111111111111","collection_id":"22222222-2222-4222-8222-222222222222","container_id":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","deadline_ns":100000000000,"kind":"bootstrap_context","owner":"cccccccccccccccccccccccccccccccc","token":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","version":1,"watchdog_pid":4242,"watchdog_start_ticks":9000}
```

```json
{"boot_id":"11111111-1111-4111-8111-111111111111","collection_id":"22222222-2222-4222-8222-222222222222","container_id":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","deadline_ns":100000000000,"kind":"bootstrap_ack","owner":"cccccccccccccccccccccccccccccccc","token":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","version":1,"watchdog_pid":4242,"watchdog_start_ticks":9000}
```

Neither packet accepts a command, path, tool request, policy override, credential,
container-name prefix, or caller-selected configuration. The ownership label is
an association reference, not a credential. Synthetic placeholder references
cannot establish actual container ownership.

### Field origins and checks without circular trust

| Value | Trusted origin and availability | Required child check |
| --- | --- | --- |
| Boot and deadline | Supervisor records the local boot and original absolute deadline before launch | Compare with the child's local boot and trusted fixed launch deadline; check the current bound |
| Bootstrap token | Trusted supervisor generates it after independent launch checks, before freezing the packet | Validate syntax and echo only after context validation/adoption; do not treat token knowledge as execution authority |
| Collection reference | Supervisor allocates a fresh reference once before launch and retains it in its immutable attempt record | Validate syntax and adopt once from the restricted channel; this does not independently prove allocation or freshness |
| Container and owner | Supervisor records an independently observed full ID and fresh label on its stopped owned container before launch | Validate syntax during adoption; independently verify exact ownership and created state before arming |
| PID and start reference | Supervisor obtains PID from its direct-child handle and start ticks from bounded kernel observation after launch | Compare against the child's actual own PID and kernel start reference, not values copied from the request |

Before any context is received, trusted launch metadata must already supply the
child's expected endpoint device/inode and resource bounds, original deadline,
and a finite bootstrap cutoff in the same host-monotonic clock domain. This is a
restricted fixed launch profile, not an externally selectable argv interface.
These prerequisites prevent an untrusted packet from choosing how long its own
receiver waits or which descriptor is adopted. Full context construction waits
until the supervisor has the retained child's independent lifetime evidence.

The bootstrap cutoff is fixed before launch, no later than the original deadline,
and subject to explicit server-owned startup limits. Both send and receive must
finish before it; equality is expiry. Re-reading, receiving a packet, adoption,
and acknowledgement never refresh it. Missing timing configuration or an
unverifiable clock-domain/boot association blocks bootstrap. These are experiment
startup limits, not gateway clock-health thresholds or production defaults.

The authoritative context deadline is the same integer `deadline_ns` later used
by the health protocol. A future adapter to the existing seconds-based watchdog
must explicitly verify that its representation cannot extend that deadline and
that the armed record matches that recorded representation. No implicit rounding,
new deadline calculation on receipt, or deadline inferred from an acknowledgement
is permitted. This conversion and its proof remain implementation prerequisites;
the existing watchdog interface and event schema are not changed here.

### Required startup order and acknowledgement meaning

1. The supervisor records immutable pre-launch context and cutoff, creates the
   private pair, and launches only the restricted reviewed child. It retains the
   direct-child handle and pidfd and closes its spare child endpoint as required.
2. The supervisor observes the independent process-start reference before the
   existing bounded progress report. The child performs checked endpoint adoption
   and explicit original-descriptor release before reporting progress. For the
   synthetic path, the only pre-context progress indication is the existing exact
   version-2 fixture report; no arbitrary packet is treated as progress. A future
   real watchdog must separately implement and validate the corresponding entry
   path. A launch handle alone does not establish command publication.
3. After that report, both full pre-health command checks bracket independent
   endpoint and original-descriptor observations, and must agree with the earlier
   start witness. Report values, boot, retained lifetime, and exit checks remain
   mandatory. Progress receipt cannot establish any of these facts by itself.
4. The supervisor freezes the exact independently established context and makes
   one whole-packet context send before the cutoff. The child accepts it only in
   its awaiting-context state, validates schema, local lifetime, boot, deadline,
   and fixed launch constraints, and adopts the immutable values once.
5. The child makes one bounded acknowledgement send containing the adopted
   context. The supervisor accepts it only while awaiting that receipt, before
   the same cutoff, after exact schema/type/value comparison with its frozen
   context and fresh independent full-command/start, boot, and exit checks. No
   packet field selects the expected PID, endpoint, container, or deadline for
   those checks.
6. Only a context-confirmed supervisor may advance its own armed-evidence and
   health admission gates. The child cannot know whether its receipt reached the
   supervisor: after its one whole-packet acknowledgement send succeeds, it may
   attempt arming only under the existing independent authorization, exact
   ownership, created-state, and deadline checks. It then persists and reports
   matching armed evidence. A failed acknowledgement send cannot newly authorize
   arming. Receipt loss after a successful send may therefore leave the child
   armed while the supervisor still treats bootstrap as unconfirmed. The watchdog
   must answer any later health challenge in its actual deadline loop. Bootstrap
   acknowledgement is neither an armed event nor a health response.
7. Any future readiness claim still requires the complete restricted handoff,
   current independent identity checks, matching armed evidence, and a valid fresh
   health exchange. Container start additionally requires every independent
   authorization, isolation, ownership, and remaining-time gate. A receipt alone
   never authorizes arming, container start, approval consumption, or tool dispatch.

The existing persisted armed event does not contain every Lifetime field. A
future integration must bind it to the immutable launch/context record through
independently validated provenance and matching existing fields; it must not
pretend that the current event contains a collection reference or start ticks.
This contract neither adds fields to that record nor declares that association
implemented. The post-reply command/lifetime, boot, and exit checks remain
required before any future admission of work.

### Phase, failure, and resource rules

The supervisor has one progression: awaiting progress, awaiting context receipt,
context confirmed, then the separate armed/health gates. It sends context only
once after progress and independent checks; awaiting context receipt means it is
waiting for the acknowledgement, not permission to send another context. The
child progresses from awaiting context to context adopted to acknowledgement
sent. Failure is terminal for the attempt. Neither side reopens bootstrap after
confirmation, accepts a second context, or initializes a second health session
to recover from a failed startup.

| Observation | Required outcome |
| --- | --- |
| Missing, malformed, oversized, wrong-version, or unexpected-kind packet | Fail the attempt; no parser fallback or partial packet acceptance |
| Duplicate context or acknowledgement, even with identical fields | Fail; no idempotent replay or readiness refresh |
| Wrong token, changed context, wrong collection, wrong lifetime, or wrong deadline in a receipt | Fail exact comparison; do not replace expected context from the packet |
| Missing or late progress, context, or acknowledgement | Stop admission under the original cutoff; no resend, new context, deadline, or child |
| Context adopted but receipt lost or send confirmation missing | Supervisor outcome remains unconfirmed; do not infer that adoption did not occur |
| Queue full or peer closure | Fail bootstrap without blocking, busy retrying, or extending time |
| Empty, different, or oversized command snapshot after progress | Preserve immediate identity rejection; no reread-until-match |
| Boot/start reference changes, child exit, or unavailable independent evidence | Fail the retained attempt; no association with another PID or process |

Temporary receive unavailability may return to bounded readiness waiting under
the unchanged cutoff; it is not a new send or a fresh budget. Every receive
reserves at most one existing bounded packet buffer. Each side retains only its
one immutable context and at most one acknowledgement; no unbounded queue,
message history, buffering of unexpected phases, or silent truncation is allowed.
Socket bounds and ancillary rejection reuse the existing transport requirements.
No negative acknowledgement, error packet, or health packet substitutes for either
bootstrap schema. Failures use a separately bounded diagnostic path, not a new
message type or a packet queued for later reinterpretation.

The supervisor must not admit work before its own context confirmation. A child
whose bootstrap failed must not newly arm. A child that adopted valid context and
completed its acknowledgement send may arm only under the separate gates above;
its supervisor may still lack the receipt. If arming may already have happened,
the supervisor's missing receipt does not prove otherwise. Preserve references
and reconcile state through the authorized path; later discovery of
adoption cannot retroactively confirm startup or permit delayed start. Any
already-armed watchdog must retain its original independent deadline obligation.
Channel loss must never disarm it. Bounded cleanup or fallback requires its own
existing authorization and independent verification, not authority inferred from
an error or an AI-generated diagnosis.

For independent review, check every field against `nts_health_protocol.Lifetime`,
compare phase handling with the current bounded transport and synthetic handoff,
and verify that no receipt substitutes for the two-event armed record or current
health. Codec tests cover syntax/equality and isolated supervisor tests cover
one-instance receipt phases. Future child-state and transport integration tests
must cover the complete failure table, no-send before independent identity
checks, exact acknowledgement matching, cutoff
expiry, lost receipt after adoption, and retained deadline action after channel
failure. These are required future proofs, not results demonstrated by this doc.

No cryptographic sender authentication, continuous executable integrity, clock
accuracy, or future responsiveness follows from context agreement. The reviewed
launch profile, descriptor confinement, controlled host, independent supervision,
and all existing trusted-time prerequisites remain necessary. Runtime approval
remains blocked, live NTS collection remains unstarted, and measurement age
remains unknown.

### Isolated bootstrap message codec

`tests/support/nts_bootstrap_codec.py` implements only the packet syntax and
exact-value comparison part of this contract. It reuses the existing internal
Lifetime validation, token-format check, duplicate-key rejection, and shared
packet ceiling. Its fixed version-1 field set prevents automatic schema expansion
if the shared lifetime model changes. The health helper's interfaces and behavior
are unchanged. CI includes the codec in the existing strict helper type check.

`BootstrapContext` holds a frozen Lifetime and token. Public operations validate
caller-created values; constructing the wrapper alone does not validate them.
`encode_context` emits deterministic compact sorted-key UTF-8 context bytes.
`decode_context` returns immutable syntax-validated assertions from complete
context packets. `encode_acknowledgement` builds matching receipt bytes without
proving that a child adopted anything. `verify_acknowledgement` returns None only
when a bounded acknowledgement matches every lifetime field and token in the
separately supplied expected context. That expected context must come from
trusted launch state, never the receipt being checked.

Both packet purposes enforce the exact ten-field schema and runtime types.
Foreign objects, invalid UTF-8, BOM, other JSON encodings, duplicate decoded keys,
missing/extra fields, nested field values, wrong versions/kinds, invalid reference
formats, booleans in integer fields, non-finite numbers, out-of-range integers,
and oversized packets fail closed. Valid JSON whitespace and key ordering are
permitted within the whole-packet limit; encoders remain deterministic. No input
is coerced, repaired, silently truncated, or accepted as a valid prefix.

Errors are fixed `BootstrapError` reasons: `INVALID_CONTEXT` for invalid supplied
expected/encoding values, `INVALID_PACKET` for rejected wire data, and
`ACKNOWLEDGEMENT_MISMATCH` for a structurally valid but different receipt. Errors
and this module's output do not echo packet contents. For a parse failure, inspect
the caller's purpose selection, exact bytes, schema, types, and size in authorized
private diagnostics. For a mismatch, compare with the retained trusted context;
never replace that context with values from the receipt. Correct the originating
error rather than loosening the schema or retrying until equality appears.

The module performs no I/O, clock reads, random-token generation, process or
container observation, transport handling, child launch, adoption, arming, or
permission decision. It tracks no session state. Repeated complete packets can
pass syntax/equality checks repeatedly. The isolated supervisor helper below
rejects duplicate receipts and wrong phases within one instance; child adoption,
transport integration, and restart/recreation rules still require implementation. Duplicate JSON
keys are rejected now, but this is distinct from duplicate-message tracking.
A positive deadline can be syntactically valid even when expired, and a token can
have valid syntax without being unpredictable. Parsing establishes neither fact.

For independent verification:

```bash
python -m pytest -q tests/test_nts_bootstrap_codec.py
MYPYPATH=tests/support python -m mypy --strict --follow-imports=silent tests/support/nts_bootstrap_codec.py
```

Focused tests check the published examples independently of the serializer,
deterministic round trips, complete packet-size boundaries, escaped duplicate
keys, exact field types and formats, invalid encodings, every receipt-field
mismatch, and rejection across bootstrap/health namespaces. They also demonstrate
that syntax/equality does not enforce time freshness or replay state. No sockets,
child processes, Docker actions, or time-source queries are needed by these tests.
Future integration must still implement every phase, provenance, cutoff, and
independent verification gate above. Live collection remains unstarted,
measurement age unknown, and approval eligibility blocked.

### Isolated supervisor bootstrap state

`tests/support/nts_bootstrap_session.py` adds single-owner in-memory phase and
cutoff checks around the existing codec. It performs no socket I/O, clock reads,
token generation, child launch, process verification, adoption, arming, Docker
action, or execution. No current launcher or watchdog calls it. CI strict-checks
it alongside all existing helpers. It does not implement the child-side state
machine or independently schedule observation of a pending attempt.

Trusted caller code constructs `BootstrapSession` with separately established
immutable expected context, explicit `started_ns`, and a fixed `cutoff_ns` no
later than that context's original deadline. Configuration requires exact bounded
integers and `started_ns < cutoff_ns <= deadline_ns`; no default or replacement
budget is provided. Actual creation after a launch must retain the previously
recorded start/cutoff, not calculate a fresh allowance. Context is validated and
encoded once during construction; only `prepare_context` releases those bytes.
None of this establishes the origin or unpredictability of the supplied values.

| Phase | Permitted progress operation | Result |
| --- | --- | --- |
| `ready` | `prepare_context(now_ns)` once | Return one complete context packet; enter `send_unconfirmed` |
| `send_unconfirmed` | `confirm_sent(now_ns)` once | Record the caller's whole-packet-send report; enter `waiting_ack` |
| `waiting_ack` | `accept_acknowledgement(packet, now_ns)` once | Validate exact matching receipt before cutoff; enter `confirmed` |
| `confirmed` | Read historical snapshot | No second preparation, confirmation, or receipt; no authority is granted |
| `failed` | Read historical snapshot | No progression or reopening |

Every progression operation calls `check(now_ns)` first. Observations must be
exact bounded integer nanoseconds and nondecreasing relative to the last accepted
observation; equal observations are permitted. While pending, equality with or
passage beyond the original cutoff permanently fails the session, even without
a packet. Trusted orchestration must call `check` independently while waiting;
the helper cannot detect time passing if no one calls it. Timing results relate
only to supplied observations; the helper does not measure validation or call
completion duration. Future orchestration must independently recheck the original
cutoff and all other gates before admitting work, including after a stalled call.
An invalid/backwards
time, wrong phase, malformed/mismatched receipt, or explicit `abort(now_ns)`
latches failure. Valid later packets and fresh time cannot recover the instance.
A missing send report does not establish that no send happened, and a missing
acknowledgement does not establish that the child did not adopt the context.

`confirmed` records a past matching receipt accepted within the cutoff. A later
`check` does not expire that historical fact, renew its cutoff, or attest current
health or remaining execution time. It is not a lease. A duplicate receipt still
fails the instance, while history retains that an earlier receipt was accepted.
All future arming, identity, health, authorization, and original-deadline gates
remain separate and mandatory. There is no readiness or approval result here.

`snapshot()` returns frozen `BootstrapSnapshot` values: phase, original expected
context, fixed cutoff, packet-release history, caller-reported send confirmation,
past acknowledgement confirmation, and the first fixed failure reason. Snapshot
reads neither observe time nor refresh the session. These are controller facts,
not independent observations of external delivery or adoption. Release history
marks the controller's release transition; an interrupted caller must not infer
that it received or transmitted the packet. Original references and earlier
confirmed facts remain available after failure for authorized investigation.
Snapshots can contain operational references and must remain private.

Normal errors use fixed `BootstrapSessionError` reasons: `INVALID_CONFIGURATION`,
`INVALID_TIME`, `BOOTSTRAP_EXPIRED`, `WRONG_PHASE`, `ACKNOWLEDGEMENT_REJECTED`,
`ACKNOWLEDGEMENT_UNAVAILABLE`, `PACKET_UNAVAILABLE`, and `BOOTSTRAP_ABORTED`.
Later progression calls on a failed instance return `SESSION_FAILED` without
replacing the first reason. An interruption during receipt validation or its
confirmation transition latches `INTERRUPTED` and propagates the interruption.
Unexpected validation errors are normalized without echoing their contents.
Trusted orchestration must abandon an interrupted operation and reconcile its
own resources; this pure helper neither owns those resources nor supplies a
hard asynchronous interruption/scheduling guarantee.

For independent verification:

```bash
python -m pytest -q tests/test_nts_bootstrap_session.py
MYPYPATH=tests/support python -m mypy --strict --follow-imports=silent tests/support/nts_bootstrap_session.py
```

Tests drive explicit synthetic times without sleeps. They cover legal order,
exclusive cutoff boundaries, expiry without receipts in each pending phase,
invalid/backwards time, early and duplicate messages, malformed and mismatched
receipts, explicit abort, unavailable/interrupted validation, immutable historical
snapshots, and inability to recover a failed instance with valid matching input.
They also demonstrate that a newly constructed instance can accept the same
packet: this module does not persist replay history or prevent attempt recreation.

For `WRONG_PHASE`, inspect the single trusted owner's call order rather than
buffering or resending a message. For timing failures, compare supplied times
with the retained original cutoff; do not reset either. For receipt failures,
inspect the codec and independently established expected context in authorized
private diagnostics. For `SESSION_FAILED`, inspect the first reason in the
snapshot and stop admission; do not construct a replacement session as recovery.
A pure failed state does not prove actual child exit or cleanup, so those require
separate authorized action and independent verification.

The helper requires a trusted single owner and is not thread-safe or resistant
to privileged in-process mutation. The future launcher must enforce one instance
per immutable attempt and fail on restart, lost observation, or ambiguous clock
provenance. These conditions, real message delivery, child adoption, armed-record
association, and independent health supervision remain unimplemented. Already
armed deadline obligations are not canceled by a local failed session. No live
NTS collection or runtime approval is enabled; measurement age remains unknown
and approval eligibility blocked.

### Isolated child bootstrap state

`tests/support/nts_child_bootstrap_session.py` complements the supervisor helper
with a pure, single-owner child-side controller. It performs no transport,
process observation, watchdog arming, or execution. Existing entry points remain
unchanged. Its tests use the actual bootstrap codec and synthetic identities.

Trusted calling code must establish the expected `Lifetime` independently before
construction. In particular, the boot, PID, and process-start values must come
from trusted local observations, and the other launch constraints must come from
trusted launch metadata. Passing packet fields straight into this constructor
would defeat the independent comparison. This helper cannot detect that misuse,
authenticate the sender, establish token unpredictability, or verify provenance.
The token is adopted from the valid context; the retained lifetime is not.

| Operation | Required phase | Result |
| --- | --- | --- |
| Construct with expected lifetime, start observation, and cutoff | New instance | Wait for one context |
| `accept_context(packet, now_ns)` | Waiting context | Validate every field, compare the complete lifetime, retain immutable context |
| `prepare_acknowledgement(now_ns)` | Adopted | Release one matching bounded receipt, with send unconfirmed |
| `confirm_sent(now_ns)` | Send unconfirmed | Record trusted caller's whole-packet send report |
| `check(now_ns)` | Any nonfailed phase | Validate supplied time; expire pending work at the fixed cutoff |
| `abort(now_ns)` | Any nonfailed phase | Permanently fail while preserving historical facts |
| `snapshot()` | Any phase | Return immutable history without renewing any deadline |

Time values must be exact bounded nonnegative integers in one trusted monotonic
domain. They cannot move backwards. The startup cutoff is fixed before input and
must satisfy `started_ns < cutoff_ns <= expected.deadline_ns`. Equality is expiry
in each pending phase, even without a new packet. Equal consecutive observations
are permitted; phase order still applies. The helper does not read a clock,
schedule checks, or measure validation/call completion time. Trusted orchestration
must enforce the original cutoff around actual I/O and completion, revalidate
current identity and all independent gates, and abandon interrupted attempts.

Malformed or mismatched input, duplicates, wrong phases, invalid time, expiry,
and explicit abort latch failure permanently. Codec errors produce
`CONTEXT_REJECTED`; unexpected validation errors produce `CONTEXT_UNAVAILABLE`
without private error text. An interruption during context validation latches
`INTERRUPTED` and propagates the interruption. Later operations raise
`SESSION_FAILED`, retaining the first reason. No reset, resend, replacement card,
or changed cutoff is supported. Retained adopted context and release/send flags
survive failure for investigation. A false send flag means no confirmation was
recorded; it does not prove no receipt was transmitted.

`confirmed` describes a caller-reported send in this instance. It cannot establish
supervisor receipt, watchdog arming, continuing health, or execution authority.
A later `check` can retain confirmed historical state after the cutoff; that is
not a renewed lease. Already armed watchdog obligations remain independent.
Recreating a controller loses its history, so it supplies no persistent or
cross-instance replay protection. Trusted single-owner Python code is required;
frozen snapshots do not protect against privileged memory modification.

For failure investigation, inspect the first snapshot reason, retained expected
lifetime, adopted context, and release/send flags privately. Compare them with
independently retained launch evidence; never repair a failed instance or infer
external absence from its flags. To check or change this implementation without
AI, inspect the phase table above against the helper's `_require`, `check`, and
`_latch` methods, then run the focused tests with the real codec:

```sh
python -m pytest -q tests/test_nts_child_bootstrap_session.py
MYPYPATH=tests/support python -m mypy --strict --follow-imports=silent tests/support/nts_child_bootstrap_session.py
```

Tests cover the complete lifetime comparison, immutable historical snapshots,
every pending cutoff, malformed input, duplicate operations, unavailable or
interrupted validation, and the deliberate lack of persistent replay protection.
Real child integration, delivery, independent identity observation, and actual
watchdog health integration remain future work. Live NTS collection is not run;
measurement age remains unknown and approval eligibility remains blocked.
