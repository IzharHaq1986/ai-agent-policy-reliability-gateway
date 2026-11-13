"""Exercise private publication using real files and injected missing receipts."""

import hashlib
import importlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

COLLECTION = "11111111-1111-4111-8111-111111111111"
REFERENCE = COLLECTION + ".json"


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    return importlib.import_module("nts_evidence_publisher")


@pytest.fixture
def store(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield directory, descriptor
    finally:
        os.close(descriptor)


def report(label="synthetic"):
    return {
        "collection_id": COLLECTION,
        "metadata": {"label": label},
        "observations": [],
    }


def readback(directory, reference):
    # A separate descriptor and reader, never the publisher's temporary handle.
    with (directory / reference).open("rb") as stream:
        value = stream.read(1048577)
    assert len(value) <= 1048576
    return value


def test_success_has_exact_readback_private_mode_and_no_temporary_file(module, store):
    directory, descriptor = store
    source = report()
    receipt = module.publish_evidence(source, directory_fd=descriptor)
    saved = readback(directory, receipt.reference)
    assert saved == module.serialize_evidence(source)
    assert receipt.byte_count == len(saved)
    assert receipt.sha256 == hashlib.sha256(saved).hexdigest()
    assert receipt.outcome == "publication_confirmed"
    assert (
        receipt.approval_eligibility == "blocked"
        and receipt.measurement_age == "unknown"
    )
    metadata = (directory / REFERENCE).stat()
    assert stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_nlink == 1
    assert list(directory.iterdir()) == [directory / REFERENCE]
    assert stat.S_ISDIR(os.fstat(descriptor).st_mode), "caller descriptor was closed"


def test_invalid_report_does_not_touch_filesystem(module, monkeypatch, store):
    directory, descriptor = store

    def forbidden(*args):
        pytest.fail("invalid report reached descriptor duplication")

    monkeypatch.setattr(module.os, "dup", forbidden)
    with pytest.raises(module.PublicationError) as error:
        module.publish_evidence({"collection_id": "../escape"}, directory_fd=descriptor)
    assert error.value.outcome == "not_published_by_attempt"
    assert error.value.reference is None and error.value.cleanup_complete
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("descriptor", [-1, True, "path-not-allowed", None])
def test_invalid_directory_descriptor(module, descriptor):
    with pytest.raises(
        module.PublicationError, match="INVALID_DIRECTORY_DESCRIPTOR"
    ) as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert error.value.reference == REFERENCE
    assert error.value.outcome == "not_published_by_attempt"


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o770, 0o500, 0o1700])
def test_unsafe_directory_permissions_prevent_file_creation(module, store, mode):
    directory, descriptor = store
    directory.chmod(mode)
    try:
        with pytest.raises(module.PublicationError, match="UNSAFE_DIRECTORY") as error:
            module.publish_evidence(report(), directory_fd=descriptor)
        assert error.value.outcome == "not_published_by_attempt"
        assert list(directory.iterdir()) == []
    finally:
        directory.chmod(0o700)


def test_wrong_directory_owner_is_rejected(module, monkeypatch, store):
    directory, descriptor = store
    uid = os.geteuid()
    monkeypatch.setattr(module.os, "geteuid", lambda: uid + 1)
    with pytest.raises(module.PublicationError, match="UNSAFE_DIRECTORY"):
        module.publish_evidence(report(), directory_fd=descriptor)
    assert list(directory.iterdir()) == []


def test_regular_file_is_not_a_directory(module, tmp_path):
    path = tmp_path / "sentinel"
    path.write_bytes(b"unchanged")
    with path.open("rb") as stream:
        with pytest.raises(module.PublicationError, match="UNSAFE_DIRECTORY"):
            module.publish_evidence(report(), directory_fd=stream.fileno())
    assert path.read_bytes() == b"unchanged"


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling", "directory"])
def test_existing_reference_never_changes(module, store, tmp_path, kind):
    directory, descriptor = store
    final = directory / REFERENCE
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"earlier-content")
    if kind == "file":
        final.write_bytes(b"earlier-content")
    elif kind == "directory":
        final.mkdir()
    else:
        final.symlink_to(sentinel if kind == "symlink" else tmp_path / "absent")
    before = final.lstat()
    with pytest.raises(module.PublicationError, match="REFERENCE_EXISTS") as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert error.value.outcome == "not_published_by_attempt"
    assert error.value.cleanup_complete
    assert final.lstat() == before
    assert sentinel.read_bytes() == b"earlier-content"
    if kind == "file":
        assert final.read_bytes() == b"earlier-content"
    assert not list(directory.glob(".nts-evidence-*.tmp"))


def test_competing_publishers_have_one_winner_and_complete_content(
    module, monkeypatch, store
):
    directory, descriptor = store
    link = module.os.link
    barrier = Barrier(2)

    def competing(*args, **kwargs):
        barrier.wait(timeout=5)
        return link(*args, **kwargs)

    monkeypatch.setattr(module.os, "link", competing)

    def publish(label):
        try:
            return label, module.publish_evidence(
                report(label), directory_fd=descriptor
            )
        except module.PublicationError as error:
            return label, error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, ["first", "second"]))
    winners = [
        (label, value)
        for label, value in outcomes
        if isinstance(value, module.PublicationReceipt)
    ]
    losers = [
        value for _, value in outcomes if isinstance(value, module.PublicationError)
    ]
    assert len(winners) == len(losers) == 1
    assert (
        losers[0].outcome == "not_published_by_attempt" and losers[0].cleanup_complete
    )
    assert readback(directory, REFERENCE) == module.serialize_evidence(
        report(winners[0][0])
    )
    assert len(list(directory.iterdir())) == 1


def test_partial_final_file_is_never_visible(module, monkeypatch, store):
    directory, descriptor = store
    write = module.os.write
    calls = []

    def short_write(fd, data):
        assert not (directory / REFERENCE).exists()
        calls.append(len(data))
        return write(fd, data[:7])

    monkeypatch.setattr(module.os, "write", short_write)
    module.publish_evidence(report(), directory_fd=descriptor)
    assert len(calls) > 1
    assert readback(directory, REFERENCE) == module.serialize_evidence(report())


@pytest.mark.parametrize("failure", ["zero", "error", "interrupt"])
def test_failed_write_leaves_no_final_report(module, monkeypatch, store, failure):
    directory, descriptor = store
    write = module.os.write

    def failed(fd, data):
        write(fd, data[:5])
        if failure == "zero":
            return 0
        if failure == "interrupt":
            raise KeyboardInterrupt()
        raise OSError("sensitive-content-must-not-leak")

    monkeypatch.setattr(module.os, "write", failed)
    with pytest.raises(module.PublicationError) as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert (
        error.value.outcome == "not_published_by_attempt"
        and error.value.cleanup_complete
    )
    assert list(directory.iterdir()) == []
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("index", [1, 2, 3, 4])
@pytest.mark.parametrize("after_real_sync", [False, True])
def test_sync_failures_never_manufacture_success(
    module, monkeypatch, store, index, after_real_sync
):
    directory, descriptor = store
    sync = module.os.fsync
    calls = []

    def failed(fd):
        calls.append(fd)
        if len(calls) == index:
            if after_real_sync:
                sync(fd)
            raise OSError("synthetic missing sync acknowledgement")
        return sync(fd)

    monkeypatch.setattr(module.os, "fsync", failed)
    with pytest.raises(module.PublicationError) as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    expected = (
        "not_published_by_attempt"
        if index < 3
        else "publication_unknown"
        if index == 3
        else "publication_confirmed"
    )
    assert error.value.outcome == expected
    assert error.value.reference == REFERENCE
    assert error.value.cleanup_complete == (index != 4)
    assert not list(directory.glob(".nts-evidence-*.tmp"))
    if index >= 3:
        assert readback(directory, REFERENCE) == module.serialize_evidence(report())
    else:
        assert not (directory / REFERENCE).exists()


@pytest.mark.parametrize("committed", [False, True])
def test_link_acknowledgement_failure_is_unknown_without_retry(
    module, monkeypatch, store, committed
):
    directory, descriptor = store
    link = module.os.link
    calls = []

    def uncertain(*args, **kwargs):
        calls.append(args)
        if committed:
            link(*args, **kwargs)
        raise OSError("synthetic lost publication acknowledgement")

    monkeypatch.setattr(module.os, "link", uncertain)
    with pytest.raises(module.PublicationError) as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert len(calls) == 1
    assert (
        error.value.outcome == "publication_unknown"
        and error.value.reference == REFERENCE
    )
    assert error.value.cleanup_complete
    assert (directory / REFERENCE).exists() == committed
    if committed:
        assert readback(directory, REFERENCE) == module.serialize_evidence(report())


@pytest.mark.parametrize("removed", [False, True])
def test_temporary_cleanup_error_prevents_success_and_never_deletes_final(
    module, monkeypatch, store, removed
):
    directory, descriptor = store
    unlink = module.os.unlink

    def uncertain(path, **kwargs):
        if removed:
            unlink(path, **kwargs)
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(module.os, "unlink", uncertain)
    try:
        with pytest.raises(
            module.PublicationError, match="CLEANUP_UNCONFIRMED"
        ) as error:
            module.publish_evidence(report(), directory_fd=descriptor)
        assert error.value.outcome == "publication_confirmed"
        assert not error.value.cleanup_complete
        assert error.value.temporary_reference is not None
        assert readback(directory, REFERENCE) == module.serialize_evidence(report())
        assert (directory / error.value.temporary_reference).exists() != removed
    finally:
        for path in directory.glob(".nts-evidence-*.tmp"):
            unlink(path.name, dir_fd=descriptor)


@pytest.mark.parametrize("index", [1, 2])
def test_close_ack_failure_is_not_retried_or_reported_as_success(
    module, monkeypatch, store, index
):
    directory, descriptor = store
    close = module.os.close
    calls = []

    def uncertain(fd):
        calls.append(fd)
        close(fd)
        if len(calls) == index:
            raise OSError("synthetic lost close acknowledgement")

    monkeypatch.setattr(module.os, "close", uncertain)
    with pytest.raises(module.PublicationError) as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert len(calls) == 2 and len(set(calls)) == 2
    assert descriptor not in calls
    assert not error.value.cleanup_complete
    assert error.value.outcome == (
        "not_published_by_attempt" if index == 1 else "publication_confirmed"
    )
    assert (directory / REFERENCE).exists() == (index == 2)


def test_directory_descriptor_remains_pinned_when_path_is_replaced(
    module, monkeypatch, store
):
    directory, descriptor = store
    moved = directory.with_name("moved")
    write = module.os.write
    changed = []

    def rename_before_write(fd, data):
        if not changed:
            directory.rename(moved)
            directory.mkdir(mode=0o700)
            changed.append(True)
        return write(fd, data)

    monkeypatch.setattr(module.os, "write", rename_before_write)
    module.publish_evidence(report(), directory_fd=descriptor)
    assert readback(moved, REFERENCE) == module.serialize_evidence(report())
    assert list(directory.iterdir()) == []


def test_temporary_collision_does_not_delete_an_unowned_file(
    module, monkeypatch, store
):
    directory, descriptor = store
    token = "a" * 32
    candidate = directory / (".nts-evidence-" + token + ".tmp")
    candidate.write_bytes(b"earlier temporary content")
    monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex=token))
    with pytest.raises(
        module.PublicationError, match="TEMPORARY_REFERENCE_EXISTS"
    ) as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert error.value.outcome == "not_published_by_attempt"
    assert candidate.read_bytes() == b"earlier temporary content"


def test_errors_do_not_print_report_or_private_paths(
    module, monkeypatch, store, capsys
):
    directory, descriptor = store
    monkeypatch.setattr(module.os, "write", lambda *args: 0)
    with pytest.raises(module.PublicationError) as error:
        module.publish_evidence(report("sensitive-payload"), directory_fd=descriptor)
    assert "sensitive" not in str(error.value)
    assert str(directory) not in str(error.value)
    assert capsys.readouterr() == ("", "")


def test_closed_descriptor_does_not_create_a_report(module, tmp_path):
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    os.close(descriptor)
    with pytest.raises(module.PublicationError, match="DIRECTORY_UNAVAILABLE") as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert error.value.outcome == "not_published_by_attempt"
    assert list(path.iterdir()) == []


@pytest.mark.parametrize("created", [False, True])
def test_unknown_temporary_creation_is_not_deleted_or_reported_clean(
    module, monkeypatch, store, created
):
    directory, descriptor = store
    open_file = module.os.open

    def uncertain(*args, **kwargs):
        if created:
            fd = open_file(*args, **kwargs)
            os.close(fd)
        raise OSError("synthetic lost creation acknowledgement")

    monkeypatch.setattr(module.os, "open", uncertain)
    with pytest.raises(
        module.PublicationError, match="TEMPORARY_CREATE_UNCONFIRMED"
    ) as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert error.value.outcome == "not_published_by_attempt"
    assert not error.value.cleanup_complete
    assert error.value.temporary_reference is not None
    assert (directory / error.value.temporary_reference).exists() == created
    assert not (directory / REFERENCE).exists()


def test_interruption_after_publication_retains_unknown_outcome_and_saved_report(
    module, monkeypatch, store
):
    directory, descriptor = store
    link = module.os.link
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(args)
        link(*args, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(module.os, "link", interrupted)
    with pytest.raises(
        module.PublicationError, match="PUBLICATION_INTERRUPTED"
    ) as error:
        module.publish_evidence(report(), directory_fd=descriptor)
    assert error.value.outcome == "publication_unknown" and error.value.cleanup_complete
    assert len(calls) == 1
    assert readback(directory, REFERENCE) == module.serialize_evidence(report())


def test_maximum_serialized_report_is_saved_without_truncation(module, store):
    directory, descriptor = store
    source = report()
    source["metadata"] = {f"pad_{index}": "a" * 32768 for index in range(31)}
    source["metadata"]["tail"] = ""
    padding = 1048576 - len(module.serialize_evidence(source))
    assert 0 < padding <= 32768
    source["metadata"]["tail"] = "a" * padding
    expected = module.serialize_evidence(source)
    assert len(expected) == 1048576
    receipt = module.publish_evidence(source, directory_fd=descriptor)
    assert receipt.byte_count == 1048576
    assert readback(directory, REFERENCE) == expected


def test_filename_is_bound_to_serialized_snapshot_not_changed_input(
    module, monkeypatch, store
):
    directory, descriptor = store
    serialize = module.serialize_evidence
    source = report()
    expected = serialize(source)

    def changed_input(value):
        payload = serialize(value)
        value["collection_id"] = "22222222-2222-4222-8222-222222222222"
        return payload

    monkeypatch.setattr(module, "serialize_evidence", changed_input)
    receipt = module.publish_evidence(source, directory_fd=descriptor)
    assert receipt.reference == REFERENCE
    assert readback(directory, REFERENCE) == expected
    assert not (directory / (source["collection_id"] + ".json")).exists()
