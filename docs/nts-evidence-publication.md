# Private diagnostic evidence publication

`tests/support/nts_evidence_publisher.py` saves one report produced by the existing
bounded serializer. It is an internal Linux building block with no CLI or default
storage path. It is not connected to a live experiment or gateway runtime.

Trusted controller code calls `publish_evidence(report, directory_fd=...)`. It
must supply an open descriptor for a private directory it is authorized to use.
The helper validates and serializes first, then duplicates the descriptor. It
checks that the duplicate identifies a directory owned by the current effective
user with mode 0700. A directory-sync preflight must succeed before file creation.
The caller retains its original descriptor; operations stay relative to the
owned duplicate even if the directory pathname changes. Caller input and its
original descriptor must not be concurrently modified or reused during the call.

The final filename is the validated collection UUID plus `.json`, derived from
the serialized snapshot rather than rereading mutable input. No arbitrary final
path, replacement ID, directory creation, or automatic retry is accepted. Reports
and operational receipts belong in private storage outside public commits.

## Publication sequence

The helper creates one random temporary name exclusively within that directory,
using no-follow file creation and owner-only mode 0600. It validates the regular
file, handles positive short writes until every byte is written, syncs the file,
and closes its descriptor before publication. Zero-progress writes or any missing
confirmation prevent progression. There is no partial final report.

A same-directory hard link publishes the complete file atomically without
replacing an existing name. Existing regular files, directories, and symlinks
cause failure and remain unchanged. The helper then syncs the directory entry,
removes only its own acknowledged temporary name, syncs that cleanup, and closes
its directory duplicate. Success is returned only after all of these operations
are acknowledged. Cleanup never unlinks a final report.

The helper has no fallback for unsupported hard links or directory sync. The
intended environment is a trusted local Linux filesystem supporting these
operations. Filesystem calls can block; this is not a bounded container-stop
mechanism. The independent watchdog and future controller retain that role.

## Outcomes and recovery

| Outcome | Meaning |
| --- | --- |
| `publication_confirmed` | Required file and publication-directory sync acknowledgements were received |
| `not_published_by_attempt` | This attempt did not create the final name; an earlier report may still exist |
| `publication_unknown` | Publication was attempted but its required acknowledgement was not received |

`PublicationReceipt` acknowledges a completely successful call. It includes the
reference, byte count, unsigned SHA-256 fingerprint, unknown measurement age, and
blocked approval eligibility. The fingerprint is a comparison aid, not a signature.

`PublicationError` carries a fixed reason code, outcome, original reference when
validated, cleanup status, and any unresolved temporary reference. It never
includes report contents or the directory pathname. Invalid reports have no
validated reference and cannot create files. Existing-reference collisions are
not idempotent success, even if their contents happen to match.

An error can have `publication_confirmed` when publication was acknowledged but
later cleanup or descriptor closure failed. That is still a failed call and must
not be treated as a successful receipt. File sync alone does not confirm the
publication directory entry. Cleanup sync does not retroactively manufacture a
missing publication acknowledgement or turn an earlier error into success.

Unknown outcomes retain the same collection reference. Reconcile through an
authorized read-only reader; do not overwrite, regenerate the ID, retry publication
automatically, or assume that an exception means no file exists. A later read can
verify visible bytes, not a missing original sync acknowledgement or physical
storage guarantees. A cleanup error may describe a temporary name already gone
when its removal acknowledgement was lost. Never delete an unacknowledged
creation candidate merely because its name resembles this helper's temporary
files. Ownership and actual state require investigation.

Handled interruptions are reported as failed publication attempts and trigger
cleanup attempts. Cleanup errors also prevent success. Descriptor close is not
retried because Linux can release a descriptor before reporting an error, allowing
that number to be reused. A killed process, repeated external interference, or a
host failure requires reconciliation; this helper cannot guarantee cleanup then.

## Validation and limits

Real filesystem tests independently reopen saved reports and compare exact bytes,
including a complete 1 MiB report. They cover owner-only permissions, pinned
directory identity, duplicate references and symlinks, short writes, exactly one
winner among competing publishers, and failures before and after publication.
Injected missing link, sync, cleanup, and close acknowledgements verify that
errors never claim confirmed non-publication after an uncertain publication and
never return a successful partial report. Tests use synthetic data only.

The assumptions include a trusted OS, filesystem implementation, and private
directory owner. Files remain modifiable by their owner or administrator; this
is not tamper-proof or signed audit storage. Sync acknowledgements are operating
system evidence, not a production hardware durability proof. Tests on disposable
or memory-backed filesystems do not establish survival of power loss.

The helper does not prove time-source identity, freshness, accuracy, uncertainty,
container cleanup, or approval authority. All reports remain diagnostic. Live
NTS orchestration and runtime approval remain blocked.

## Primary references

- [Python 3.12 descriptor-relative filesystem operations](https://docs.python.org/3.12/library/os.html)
- [Linux fsync and directory-entry confirmation](https://man7.org/linux/man-pages/man2/fsync.2.html)
- [Linux close errors and descriptor reuse](https://man7.org/linux/man-pages/man2/close.2.html)
