"""Read-only identity checks for one restricted local Docker workload."""

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from nts_command_capture import CaptureError, OutputBudget, capture
from nts_container_watchdog import LABEL, WatchdogError, boot_id

_PROC = Path("/proc")
_LIMITS = {"stat": 4096, "status": 16384, "cmdline": 4096, "cgroup": 4096}
_CAPS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
_FORMAT = (
    '{"Id":{{json .Id}},"Image":{{json .Image}},"User":{{json .Config.User}},'
    '"Labels":{{json .Config.Labels}},"State":{{json .State}},'
    '"RestartCount":{{json .RestartCount}}}'
)
_DOCKER = (
    "/usr/bin/env",
    "-u",
    "DOCKER_CONTEXT",
    "-u",
    "DOCKER_HOST",
    "-u",
    "DOCKER_TLS_VERIFY",
    "-u",
    "DOCKER_CERT_PATH",
    "docker",
    "--host",
    "unix:///var/run/docker.sock",
)


class IdentityError(RuntimeError):
    """Carry only a fixed reason, never process arguments or inspection output."""


@dataclass(frozen=True)
class ExpectedWorkload:
    container_id: str
    image_id: str
    owner: str
    command: tuple[str, ...]
    host_boot_id: str


@dataclass(frozen=True)
class WorkloadSnapshot:
    expected: ExpectedWorkload
    host_pid: int
    container_started_at: str
    restart_count: int
    process_start_ticks: int
    command: tuple[str, ...]
    uid: tuple[int, ...]
    gid: tuple[int, ...]
    capabilities: tuple[int, ...]
    no_new_privs: int
    cgroup_sha256: str
    started_monotonic: float
    ended_monotonic: float
    measurement_age: Literal["unknown"] = "unknown"
    approval_eligibility: Literal["blocked"] = "blocked"


def _text(value: object, pattern: str) -> bool:
    return (
        type(value) is str
        and len(value) <= 128
        and re.fullmatch(pattern, value) is not None
    )


def _positive(value: object) -> bool:
    return type(value) is int and 0 < value < (1 << 63)


def _expected(value: ExpectedWorkload) -> None:
    if type(value) is not ExpectedWorkload or not (
        _text(value.container_id, r"[0-9a-f]{64}")
        and _text(value.image_id, r"sha256:[0-9a-f]{64}")
        and _text(value.owner, r"[0-9a-f]{32}")
        and _text(value.host_boot_id, r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")
        and type(value.command) is tuple
        and 0 < len(value.command) <= 32
        and all(
            type(arg) is str and "\0" not in arg and len(arg) <= 4096
            for arg in value.command
        )
    ):
        raise IdentityError("INVALID_EXPECTED_IDENTITY")
    try:
        command = b"\0".join(arg.encode("utf-8") for arg in value.command) + b"\0"
    except UnicodeError:
        raise IdentityError("INVALID_EXPECTED_IDENTITY") from None
    if not value.command[0] or len(command) > _LIMITS["cmdline"]:
        raise IdentityError("INVALID_EXPECTED_IDENTITY")


def _deadline(deadline: float) -> None:
    if type(deadline) not in (int, float):
        raise IdentityError("INVALID_DEADLINE")
    try:
        valid = math.isfinite(deadline)
    except OverflowError:
        valid = False
    if not valid:
        raise IdentityError("INVALID_DEADLINE")
    if time.monotonic() >= deadline:
        raise IdentityError("IDENTITY_TIMEOUT")


def _start_reference(value: object) -> bool:
    if not _text(
        value, r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z"
    ):
        return False
    try:
        datetime.fromisoformat(str(value))
    except ValueError:
        return False
    return not str(value).startswith("0001-")


def _inspect(
    expected: ExpectedWorkload, deadline: float, budget: OutputBudget
) -> tuple[int, str, int]:
    output = capture(
        (*_DOCKER, "container", "inspect", "--format", _FORMAT, expected.container_id),
        deadline=deadline,
        budget=budget,
    )
    try:
        data = json.loads(output.stdout)
        state = data["State"]
        pid, started, restarts = state["Pid"], state["StartedAt"], data["RestartCount"]
        valid = (
            data["Id"] == expected.container_id
            and data["Image"] == expected.image_id
            and data["Labels"][LABEL] == expected.owner
            and data["User"] == "10001:10001"
            and state["Status"] == "running"
            and state["Running"] is True
            and state["Paused"] is False
            and state["Restarting"] is False
            and state["Dead"] is False
            and state["OOMKilled"] is False
            and state["Error"] == ""
            and _positive(pid)
            and pid < (1 << 31)
            and _start_reference(started)
            and type(restarts) is int
            and 0 <= restarts < (1 << 31)
        )
    except (ValueError, KeyError, TypeError):
        raise IdentityError("MALFORMED_DOCKER_IDENTITY") from None
    if not valid:
        raise IdentityError("DOCKER_IDENTITY_REJECTED")
    return pid, started, restarts


def _read(directory: int, name: str, deadline: float, budget: OutputBudget) -> bytes:
    local_deadline = min(deadline, time.monotonic() + 2)
    _deadline(local_deadline)
    if budget.remaining <= 0:
        raise IdentityError("IDENTITY_OUTPUT_LIMIT")
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
    )
    try:
        data = bytearray()
        while True:
            _deadline(local_deadline)
            room = min(_LIMITS[name] - len(data), budget.remaining)
            chunk = os.read(descriptor, min(4096, room + 1))
            budget._charge(len(chunk))
            if len(chunk) > room:
                raise IdentityError("IDENTITY_OUTPUT_LIMIT")
            if not chunk:
                break
            data.extend(chunk)
        _deadline(local_deadline)
        return bytes(data)
    finally:
        os.close(descriptor)


def _ticks(raw: bytes, pid: int) -> int:
    prefix, separator, tail = raw.rpartition(b")")
    if not separator or not prefix.startswith(str(pid).encode() + b" ("):
        raise IdentityError("MALFORMED_PROCESS_STAT")
    fields = tail.split()
    if (
        len(fields) < 20
        or fields[0] not in (b"R", b"S", b"D")
        or not fields[19].isdigit()
    ):
        raise IdentityError("MALFORMED_PROCESS_STAT")
    ticks = int(
        fields[19]
    )  # Field 22; field 3 starts after the final closing parenthesis.
    if not _positive(ticks):
        raise IdentityError("MALFORMED_PROCESS_STAT")
    return ticks


def _process(
    directory: int,
    pid: int,
    expected: ExpectedWorkload,
    deadline: float,
    budget: OutputBudget,
) -> tuple[int, tuple[str, ...], str]:
    first_ticks = _ticks(_read(directory, "stat", deadline, budget), pid)
    command = _read(directory, "cmdline", deadline, budget)
    status = _read(directory, "status", deadline, budget)
    cgroup = _read(directory, "cgroup", deadline, budget)
    # Recheck the relevant observations inside one pinned /proc directory.
    if (
        command != _read(directory, "cmdline", deadline, budget)
        or status_fields(status)
        != status_fields(_read(directory, "status", deadline, budget))
        or cgroup != _read(directory, "cgroup", deadline, budget)
        or first_ticks != _ticks(_read(directory, "stat", deadline, budget), pid)
    ):
        raise IdentityError("PROCESS_IDENTITY_CHANGED")
    expected_bytes = b"\0".join(arg.encode() for arg in expected.command) + b"\0"
    if command != expected_bytes:
        raise IdentityError("COMMAND_CHANGED")
    fields = status_fields(status)
    if (
        fields["Pid"] != str(pid)
        or fields["Uid"].split() != ["10001"] * 4
        or fields["Gid"].split() != ["10001"] * 4
    ):
        raise IdentityError("PROCESS_PRIVILEGES_REJECTED")
    if fields["NoNewPrivs"] != "1" or any(
        not re.fullmatch(r"0{16}", fields[key]) for key in _CAPS
    ):
        raise IdentityError("PROCESS_PRIVILEGES_REJECTED")
    matched = False
    for line in cgroup.decode("ascii").splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[2].startswith("/"):
            raise IdentityError("MALFORMED_CGROUP")
        components = parts[2].split("/")
        matched |= (
            expected.container_id in components
            or f"docker-{expected.container_id}.scope" in components
        )
    if not matched:
        raise IdentityError("LOCAL_PROCESS_MAPPING_UNVERIFIED")
    return first_ticks, expected.command, hashlib.sha256(cgroup).hexdigest()


def status_fields(raw: bytes) -> dict[str, str]:
    wanted = {"Pid", "Uid", "Gid", "NoNewPrivs", *_CAPS}
    result = {}
    for line in raw.decode("ascii").splitlines():
        key, separator, value = line.partition(":")
        if key in wanted:
            if not separator or key in result:
                raise IdentityError("MALFORMED_PROCESS_STATUS")
            result[key] = value.strip()
    if result.keys() != wanted:
        raise IdentityError("MALFORMED_PROCESS_STATUS")
    return result


def snapshot(
    expected: ExpectedWorkload, *, deadline: float, budget: OutputBudget
) -> WorkloadSnapshot:
    _expected(expected)
    _deadline(deadline)
    if type(budget) is not OutputBudget:
        raise IdentityError("INVALID_OUTPUT_BUDGET")
    started = time.monotonic()
    try:
        if boot_id() != expected.host_boot_id:
            raise IdentityError("HOST_BOOT_CHANGED")
        before = _inspect(expected, deadline, budget)
        directory = os.open(
            _PROC / str(before[0]),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            ticks, command, cgroup = _process(
                directory, before[0], expected, deadline, budget
            )
            after = _inspect(expected, deadline, budget)
            # Recheck the pinned process after Docker's identity observation.
            if before != after or ticks != _ticks(
                _read(directory, "stat", deadline, budget), before[0]
            ):
                raise IdentityError("WORKLOAD_CHANGED")
        finally:
            os.close(directory)
        if boot_id() != expected.host_boot_id:
            raise IdentityError("HOST_BOOT_CHANGED")
        _deadline(deadline)
    except (CaptureError, WatchdogError, OSError, UnicodeError, ValueError):
        raise IdentityError("IDENTITY_UNAVAILABLE") from None
    ended = time.monotonic()
    if ended >= deadline:
        raise IdentityError("IDENTITY_TIMEOUT")
    return WorkloadSnapshot(
        expected,
        before[0],
        before[1],
        before[2],
        ticks,
        command,
        (10001,) * 4,
        (10001,) * 4,
        (0,) * 5,
        1,
        cgroup,
        started,
        ended,
    )


def require_same(
    expected: ExpectedWorkload, before: WorkloadSnapshot, after: WorkloadSnapshot
) -> None:
    _expected(expected)
    for item in (before, after):
        if type(item) is not WorkloadSnapshot:
            raise IdentityError("INVALID_SNAPSHOT")
        _expected(item.expected)
        if not (
            type(item.expected) is ExpectedWorkload
            and item.expected == expected
            and _positive(item.host_pid)
            and item.host_pid < (1 << 31)
            and _start_reference(item.container_started_at)
            and _positive(item.process_start_ticks)
            and type(item.restart_count) is int
            and 0 <= item.restart_count < (1 << 31)
            and type(item.command) is tuple
            and len(item.command) == len(expected.command)
            and all(type(arg) is str for arg in item.command)
            and item.command == expected.command
            and type(item.uid) is tuple
            and len(item.uid) == 4
            and all(type(number) is int for number in item.uid)
            and item.uid == (10001,) * 4
            and type(item.gid) is tuple
            and len(item.gid) == 4
            and all(type(number) is int for number in item.gid)
            and item.gid == (10001,) * 4
            and type(item.capabilities) is tuple
            and len(item.capabilities) == 5
            and all(type(number) is int for number in item.capabilities)
            and item.capabilities == (0,) * 5
            and type(item.no_new_privs) is int
            and item.no_new_privs == 1
            and _text(item.cgroup_sha256, r"[0-9a-f]{64}")
            and type(item.started_monotonic) is float
            and math.isfinite(item.started_monotonic)
            and type(item.ended_monotonic) is float
            and math.isfinite(item.ended_monotonic)
            and 0 <= item.started_monotonic <= item.ended_monotonic
            and type(item.measurement_age) is str
            and item.measurement_age == "unknown"
            and type(item.approval_eligibility) is str
            and item.approval_eligibility == "blocked"
        ):
            raise IdentityError("INVALID_SNAPSHOT")
    if after.started_monotonic < before.ended_monotonic:
        raise IdentityError("SNAPSHOT_ORDER_INVALID")
    if (
        before.host_pid,
        before.container_started_at,
        before.restart_count,
        before.process_start_ticks,
        before.command,
        before.uid,
        before.gid,
        before.capabilities,
        before.no_new_privs,
        before.cgroup_sha256,
    ) != (
        after.host_pid,
        after.container_started_at,
        after.restart_count,
        after.process_start_ticks,
        after.command,
        after.uid,
        after.gid,
        after.capabilities,
        after.no_new_privs,
        after.cgroup_sha256,
    ):
        raise IdentityError("WORKLOAD_CHANGED")
