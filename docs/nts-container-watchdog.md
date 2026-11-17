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
   bootstrap message on the inherited endpoint. Its schema and adoption checks
   require separate implementation; do not reinterpret bootstrap as a health
   reply. The child checks its own lifetime against that context. Only after
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
