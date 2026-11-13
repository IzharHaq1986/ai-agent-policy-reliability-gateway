# Restricted local workload identity

`tests/support/nts_workload_identity.py` is a read-only internal collector and
comparison helper. It does not start or restart a container, contact a time
source, print process data, persist reports, or authorize approval.

Trusted controller code supplies an `ExpectedWorkload`: full container ID, image
ID, ownership token, exact command tuple, and host boot ID. No short IDs, image
tags, arbitrary endpoints, shell commands, or namespace guesses are accepted.
Command arguments are comparison data only and are never executed by this helper.

## Snapshot collection

`snapshot(expected, deadline=..., budget=...)` reuses the caller's original
monotonic deadline and shared `OutputBudget`. It queries the fixed local Docker
Unix socket, ignoring endpoint/context/TLS environment overrides for that read.
The fixed command uses bounded capture: no shell, at most two seconds and 32 KiB
combined output. The expected boot ID must match before and after collection.

Docker identity includes the image, ownership label, user, running state, host
PID, start reference, and restart counter. Paused, restarting, dead, stopped, OOM,
or error states are rejected. The start reference retains its full reported
fractional precision; it is an identity string, not trusted current UTC time.

The helper opens a pinned `/proc/PID` directory and performs bounded relative
reads without following file symlinks. A reused numeric PID cannot redirect
those reads to a newly opened process directory. It observes kernel process-start
ticks, command-line bytes, relevant UID/GID and capability fields, no-new-privileges,
and cgroup membership. Stat parsing handles spaces and parentheses in process
names; changing CPU or scheduling counters are not identity changes.

Supported workloads have host-visible UID/GID 10001, all five reported capability
sets empty, no-new-privileges enabled, the exact expected command, and a cgroup
path component containing the full Docker ID or its systemd Docker scope name.
This corroborates the local Docker-to-host-process mapping. The helper does not
infer or translate user-namespace mappings. Rootless, remapped, remote, hidden-proc,
or unfamiliar cgroup layouts that cannot satisfy this exact host-visible profile
fail explicitly. Denied access or an exited process is unavailable evidence, not
a valid snapshot.

Relevant process observations are rechecked inside the pinned directory. Docker
identity is read again, then process-start and boot identity are checked again.
A changed generation, ownership, command, privileges, or mapping rejects the
snapshot. Individual file reads have 4 KiB limits for stat, command line, and
cgroup, and 16 KiB for status. Reads consume the same raw-output budget as Docker
queries; size overflow or a missing deadline prevents a successful partial result.
File reads check a two-second local allowance within the shared deadline, but a
blocked kernel operation cannot be interrupted by these checks alone. Independent
stop supervision remains necessary.

## Comparison and limits

`require_same(expected, before, after)` validates each snapshot's structure and
restricted profile, requires ordered nonoverlapping collection intervals, and
compares workload-generation fields. Matching a container ID alone is insufficient.
Host PID reuse, restart count, wall-clock start text, and process-start ticks are
not used individually as the only identity reference. Observation interval times
are excluded from identity equality but are validated and ordered.

The returned snapshots remain diagnostic: measurement age unknown and approval
eligibility blocked. They are intended to come directly from trusted internal
collection. Structural validation does not authenticate a snapshot received from
an untrusted producer. Point observations are not continuous monitoring, executable
attestation, or protection against a privileged administrator. Same-process
re-execution or masquerading that preserves all observed identifiers is not ruled
out. Matching observations cannot establish source authentication, freshness,
accuracy, uncertainty, watchdog health, or approval authority.

## Synthetic Docker proof

`tests/support/run_nts_identity_acceptance.py --accept` reuses the existing gateway
image and harmless sleep fixture. It requires the local default Docker context,
rejects endpoint override variables without changing them, and checks the fixture's
non-root user, disabled network, no mounts/ports, dropped capabilities,
no-new-privileges, read-only filesystem, bounded resources/logging, and no restart
policy before starting it. Build-time dependency access follows the existing
image build pattern; the workload contacts no external service.

Two unchanged snapshots must match. An independent Docker/kernel reader verifies
the observed generation. The harness then restarts only that owned fixture,
verifies the new generation independently, and requires rejection of the earlier
snapshot despite the same container ID. A separately labelled derived test copy
aligns only the restart counter and must still be rejected. Actual observed
counters are preserved in the output; the derived copy is not represented as
original evidence.

A successful run reports unchanged-workload, restart-rejection, counter-independence,
fixture cleanup, image cleanup, and final Docker acceptance markers. Ownership
references are printed before creation so uncertain creation can be reconciled.
Cleanup resolves exact IDs by the unique label, verifies ownership before
removal, and independently confirms absence. Unverified cleanup prevents PASS.
Only its own image tag and fixture are removed; no prune commands are used.

On Hetzner, after `sudo -v`, run the dedicated synthetic harness with consistent
host privileges:

    sudo -n -- /usr/bin/env PYTHONDONTWRITEBYTECODE=1 "$HOME/gateway-approval-validation-venv/bin/python" tests/support/run_nts_identity_acceptance.py --accept

Fixtures remain non-root. Local unit tests use explicitly synthetic Docker/proc
observations and do not establish real Docker acceptance. No live chrony/NTS
workload or gateway approval path is wired by this slice. Full controller health,
identity supervision, source/sample correlation, timing-discontinuity handling,
and network cleanup still need implementation and validation.

## Primary references

- [Linux process stat fields](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)
- [Linux process status and privilege fields](https://man7.org/linux/man-pages/man5/proc_pid_status.5.html)
- [Docker container inspection](https://docs.docker.com/reference/cli/docker/container/inspect/)
