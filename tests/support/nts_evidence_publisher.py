"""Privately publish one diagnostic report without replacing an existing name."""

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from nts_evidence_bundle import EvidenceError, serialize_evidence

type Outcome = Literal[
    "publication_confirmed", "not_published_by_attempt", "publication_unknown"
]


class PublicationError(RuntimeError):
    """Retain recovery references and acknowledgement state, never report content."""

    def __init__(
        self,
        reason: str,
        outcome: Outcome,
        reference: str | None,
        *,
        cleanup_complete: bool,
        temporary_reference: str | None = None,
    ):
        super().__init__(reason)
        self.outcome = outcome
        self.reference = reference
        self.cleanup_complete = cleanup_complete
        self.temporary_reference = temporary_reference


@dataclass(frozen=True)
class PublicationReceipt:
    """Acknowledge OS operations, not hardware durability or time-source trust."""

    reference: str
    byte_count: int
    sha256: str
    outcome: Literal["publication_confirmed"] = "publication_confirmed"
    measurement_age: Literal["unknown"] = "unknown"
    approval_eligibility: Literal["blocked"] = "blocked"


def publish_evidence(report: object, *, directory_fd: int) -> PublicationReceipt:
    """Use a trusted private directory descriptor; never choose a path or retry."""
    try:
        payload = serialize_evidence(report)
    except EvidenceError:
        raise PublicationError(
            "INVALID_REPORT",
            "not_published_by_attempt",
            None,
            cleanup_complete=True,
        ) from None
    # Derive the name from the serialized snapshot, not a second read of mutable
    # caller input. The serializer already validated this canonical UUID.
    reference = json.loads(payload)["collection_id"] + ".json"
    receipt = PublicationReceipt(
        reference, len(payload), hashlib.sha256(payload).hexdigest()
    )
    if type(directory_fd) is not int or directory_fd < 0:
        raise PublicationError(
            "INVALID_DIRECTORY_DESCRIPTOR",
            "not_published_by_attempt",
            reference,
            cleanup_complete=True,
        )
    owned_directory = None
    file_fd = None
    temporary = None
    created = False
    temporary_unknown = False
    directory_dirty = False
    cleanup_complete = True
    outcome: Outcome = "not_published_by_attempt"
    reason = None
    stage = "DIRECTORY_UNAVAILABLE"
    try:
        # Pin a separate descriptor. The caller's descriptor is never closed.
        owned_directory = os.dup(directory_fd)
        stage = "UNSAFE_DIRECTORY"
        metadata = os.fstat(owned_directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError("Unsafe private directory")
        stage = "DIRECTORY_SYNC_UNCONFIRMED"
        os.fsync(owned_directory)  # Reject unsupported directory sync before writing.
        temporary = ".nts-evidence-" + uuid4().hex + ".tmp"
        stage = "TEMPORARY_CREATE_UNCONFIRMED"
        try:
            file_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=owned_directory,
            )
        except FileExistsError:
            stage = "TEMPORARY_REFERENCE_EXISTS"
            raise
        except BaseException:
            # Do not delete a candidate whose creation was not acknowledged.
            temporary_unknown = True
            raise
        created = True
        directory_dirty = True
        stage = "UNSAFE_TEMPORARY_FILE"
        os.fchmod(
            file_fd, 0o600
        )  # Keep exact permissions even under a restrictive umask.
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ValueError("Unsafe temporary file")
        stage = "WRITE_UNCONFIRMED"
        remaining = memoryview(payload)
        while remaining:
            count = os.write(file_fd, remaining)
            if type(count) is not int or not 0 < count <= len(remaining):
                raise OSError("Incomplete report write")
            remaining = remaining[count:]
        stage = "FILE_SYNC_UNCONFIRMED"
        os.fsync(file_fd)
        stage = "FILE_CLOSE_UNCONFIRMED"
        closing, file_fd = file_fd, None
        try:
            # Never retry close: its descriptor may already be released.
            os.close(closing)
        except BaseException:
            cleanup_complete = False
            raise
        stage = "PUBLICATION_UNCONFIRMED"
        outcome = "publication_unknown"
        try:
            # A same-directory hard link atomically publishes the complete file
            # and refuses an existing name, including a symlink. Never replace.
            os.link(
                temporary,
                reference,
                src_dir_fd=owned_directory,
                dst_dir_fd=owned_directory,
                follow_symlinks=False,
            )
        except FileExistsError:
            outcome = "not_published_by_attempt"
            stage = "REFERENCE_EXISTS"
            raise
        stage = "DIRECTORY_SYNC_UNCONFIRMED"
        os.fsync(owned_directory)
        outcome = "publication_confirmed"
    except BaseException as error:
        # Interruption is also a failed attempt, with references for recovery.
        reason = (
            "PUBLICATION_INTERRUPTED"
            if isinstance(error, (KeyboardInterrupt, SystemExit))
            else stage
        )
    finally:
        if file_fd is not None:
            created = True
            closing, file_fd = file_fd, None
            try:
                os.close(closing)
            except BaseException:
                cleanup_complete = False
        if created and temporary is not None and owned_directory is not None:
            try:
                os.unlink(temporary, dir_fd=owned_directory)
                created = False
            except BaseException:
                cleanup_complete = False
        if directory_dirty and owned_directory is not None:
            try:
                # Cleanup sync cannot retroactively manufacture a missing
                # publication acknowledgement or turn an error into success.
                os.fsync(owned_directory)
            except BaseException:
                cleanup_complete = False
        if owned_directory is not None:
            closing, owned_directory = owned_directory, None
            try:
                os.close(closing)
            except BaseException:
                cleanup_complete = False
        cleanup_complete = cleanup_complete and not temporary_unknown
    if reason is not None or not cleanup_complete:
        raise PublicationError(
            reason or "CLEANUP_UNCONFIRMED",
            outcome,
            reference,
            cleanup_complete=cleanup_complete,
            temporary_reference=temporary if created or temporary_unknown else None,
        ) from None
    return receipt
