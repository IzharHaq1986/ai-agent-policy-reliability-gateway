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
