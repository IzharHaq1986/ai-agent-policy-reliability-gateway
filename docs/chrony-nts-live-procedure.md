# Review specification for an isolated live NTS capability experiment

Status: procedure reviewed against the recorded experiment constraints; not an
executable runner and not evidence of a live result. Execution remains gated on
an implemented and validated controller, explicit opt-in, and recorded review.
The existing preparation command must not be used as a live runner.

## Fixed inputs

Use the prepared Hetzner image ID:

`sha256:726657be713c3585907ac751907f0aebcbc95e1931451776767046e7d52edac1`

Expected configuration SHA-256:

`080501939ac0f5536b1feeb9d482b3ddef42dcef824123f336b04aee5cd862e5`

The endpoint is `time.cloudflare.com`; chrony is `4.5-1ubuntu4.2` with NTS support.
The saved preparation report is the package-version reference. Do not rebuild,
pull, install packages, substitute an image, or change the host time service.

Verify the local image ID, linux/amd64 architecture, user `10001:10001`, package
manifest, configuration hash, and entrypoint against that report before launch.
Use an offline inspection container with the preparation restrictions, then
remove it. No result from this inspection establishes live authentication.

## Isolation and ownership

Create a uniquely named, owned bridge network and a stopped container attached
only to it. Use an ordinary private Docker bridge with outbound access; an
internal-only bridge would prevent the required external exchange. Do not use
host networking, published ports, host mounts, devices, persistent volumes, or
additional network attachments. This network is isolation, not an endpoint
firewall. DNS and NTS can select an NTP address different from the configured
hostname; record the association rather than trusting reverse DNS.

Retain these checked settings:

- Non-root user `10001:10001`, all Linux capabilities dropped, and no privilege escalation.
- Read-only root filesystem and the image's fixed entrypoint including `chronyd -x`.
- One CPU, 256 MiB memory, 64 processes, and no automatic restart.
- Two temporary filesystems, each 16 MiB, at `/run/nts` and `/tmp`, owned by UID/GID 10001, with nosuid, nodev, noexec, and the prepared permissions.
- Bounded Docker logging, at most one 1 MiB file; never enable packet, key, or cookie dumps.

Inspect the actual stopped container before starting it. Verify image, command,
network attachments, capabilities, security options, mounts, logging, and resource
limits. A mismatch prevents launch. Persist owned resource IDs immediately so
cleanup does not depend on later name lookup or successful collection.

## Deadline and collection

The future controller must arm an independent stop watchdog before starting the
container. Establish one host monotonic deadline of 120 seconds before the start
request; start latency and all collection attempts count against that budget.
No retry may reset it. Each query has a timeout of the smaller of 2 seconds and
the remaining budget. Stop starting queries when no budget remains. The watchdog
must stop the owned container at the deadline even if the collector is blocked.

A controller timeout alone does not prove that Docker stopped the daemon. Check
actual stopped state before reporting successful deadline enforcement. A failed
stop, unavailable Docker daemon, host suspension, or interrupted controller is
an experiment failure requiring reconciliation and cleanup. Do not claim a
hard real-time guarantee under host failure. Cleanup may continue after the live
budget; it must not permit continued measurement collection or a success claim.

Capture host boot ID and collection ID, container ID, image ID, container start
reference, restart count, host PID, daemon command line, and effective privileges
before and after each evidence round. Container restart, daemon replacement,
identity changes, host boot changes, or observed timing discontinuities invalidate
the round. Do not restart automatically or combine evidence across lifetimes.

The fixed monitoring interface is `/usr/bin/chronyc -n -h /run/nts/chronyd.sock`
inside the owned container. Use only these read-only reports:

| Report | Evidence sought |
| --- | --- |
| `sources -v` | Numeric address, selection state, good-sample age, offset, reported error margin |
| `sourcename ADDRESS` | Original configured source name for that numeric address |
| `authdata` | NTS mechanism, key-establishment indicator, cookies available, establishment age |
| `ntpdata ADDRESS` | Response authentication, accepted/valid packet counters, packet fields, delay and dispersion |
| `tracking` | Selected reference, reported update time, offsets, skew, delay, dispersion, leap status |

`ADDRESS` must be one parsed numeric IP from the current source report, validated
with an IP parser and passed as an argument without a shell. Never accept a
caller-supplied command, socket, hostname, address, or additional chronyc option.
Use the explicit Unix socket; do not fall back to host monitoring or UDP.

Timestamp each query's start and end with local monotonic readings. Preserve
exit status and raw stdout/stderr, capped during reading at 32 KiB combined per
query and 1 MiB for the complete evidence bundle. Overflow, timeout, denied
access, missing tools, malformed fields, or truncated output invalidates the
round. Do not parse unbounded output and truncate it afterward. Read bounded
daemon logs for certificate, key-establishment, startup, and fatal errors.

At most one evidence round starts per second. Bracket authentication and tracking
reports with source-specific `ntpdata` and `sources` observations. Record numeric
source identity, response and accepted-sample counters, key-establishment state,
and timing metadata at both ends. A changed address, key state, counters, or
sample reference makes the round non-atomic; discard it and try another complete
round only within the original deadline. Missing correlation is inconclusive.
Even unchanged counters do not create a measurement-generation timestamp.

## Interpretation and outcome

Certificate validation must remain enabled with the prepared CA trust store and
configured hostname. Never use `nocerttimecheck`, disable hostname checks, add an
unauthenticated fallback, or adjust the host clock to make validation succeed.
Record the validation configuration and successful NTS establishment evidence;
absence of an error alone is insufficient. If detailed peer-certificate evidence
is unavailable through this interface, say so explicitly. Do not claim certificate
pinning or independent validation of the remote server's clock.

A successful capability observation requires the same source and lifetime to
show NTS establishment, authenticated responses, accepted measurements, and
selection as the reference, with interpretable timing and uncertainty fields.
Do not equate a positive historical good-packet counter with proof that the last
valid response was the accepted sample. Require evidence connecting them; if the
available reports cannot establish that connection, report the gap and an
inconclusive outcome. A selected source in `-x` mode does not mean the host clock
was synchronized by the experiment.

Keep these meanings separate:

- `authdata Last` describes time since key establishment.
- `sources LastRx` describes time since receipt of a good sample.
- Query duration describes collection time, not sample generation or delivery age.
- Reported offsets, error margins, dispersion, and skew are observations, not approved gateway tolerances.

No gateway freshness or uncertainty threshold is introduced here. Do not derive
a safe measurement-age bound from these reports alone. Source accuracy, network
delay bounds, uncertainty growth, and operational limits remain unresolved.

| Outcome | Required report |
| --- | --- |
| Correlated authenticated capability observed | Report that limited observation and remaining evidence gaps |
| Authentication or isolation failure | Failed experiment; no fallback |
| Deadline without enough evidence or insufficient correlation | Inconclusive experiment |
| Cleanup or deadline enforcement cannot be verified | Failed overall run, even if useful observations were captured |

Every report retains `measurement_age: unknown` and
`approval_eligibility: blocked`. A capability success never enables approvals.
Do not emit a single generic PASS that hides these distinctions.

## Stop, preserve, and clean up

On completion, timeout, collection error, or handled interruption, stop and remove
only the recorded owned container, then remove its dedicated network. Removing
the container removes its temporary runtime state. Remove any controller-owned
temporary configuration and local temporary files. Preserve the prepared image
and the bounded evidence bundle deliberately. Do not use broad prune commands.

Independently query Docker after removal. Confirm the exact owned container and
network are absent using a successful inventory query; an inspection error alone
cannot distinguish absence from denied access or an unavailable daemon. Verify
owned temporary paths are absent. Failed cleanup is recorded explicitly and
prevents an overall successful result. After an unhandled kill or host restart,
reconcile recorded resource IDs before any new experiment.

## Review gate and remaining work

The document defines inputs, isolation, observation, failure, and cleanup paths.
No live command has been issued by this review. Before execution, implement and
validate the bounded controller, independent watchdog, streaming output limits,
identity checks, correlation rules, interruption handling, and absence checks.
Use focused failure tests plus the existing required checks. Record that next
implementation scope separately; this documentation does not authorize silently
substituting an ad hoc shell launch.

## Primary references

The chrony manuals define the command options and report meanings used here.
NTS authentication has security limits independent of source accuracy and delay.

- [chronyd 4.5](https://chrony-project.org/doc/4.5/chronyd.html)
- [chronyc 4.5](https://chrony-project.org/doc/4.5/chronyc.html)
- [chrony 4.5 configuration](https://chrony-project.org/doc/4.5/chrony.conf.html)
- [NTS security considerations](https://www.rfc-editor.org/rfc/rfc8915.html#section-9)

The [internal command-capture helper](nts-command-capture.md) now provides a
separate, tested building block for query deadlines and streaming output bounds.
It is not integrated into a live controller and does not satisfy the independent
container-stop or complete evidence-bundle requirements by itself.

The [independent container watchdog](nts-container-watchdog.md) adds a separate
deadline-removal building block and synthetic Docker proof. It does not launch
the live experiment or complete the remaining controller requirements.

The [bounded evidence serializer](nts-evidence-bundle.md) separately limits the
complete encoded diagnostic report. It performs no file persistence or live
integration and cannot establish trusted time.
