"""Opt-in independent deadline removal of one labelled disposable container."""

import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import TextIO

from nts_command_capture import CaptureError, OutputBudget, capture

LABEL = "org.khyber.gateway.nts-experiment-owner"
MAX_SECONDS = 120.0
CLEANUP_SECONDS = 10.0


class WatchdogError(RuntimeError):
    """Carry a fixed failure code without Docker output or credential values."""


def boot_id() -> str:
    with open("/proc/sys/kernel/random/boot_id", "rb") as stream:
        value = stream.read(65).decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is None:
        raise WatchdogError("INVALID_BOOT_ID")
    return value


def query(arguments: tuple[str, ...], deadline: float, budget: OutputBudget) -> str:
    result = capture(("docker", *arguments), deadline=deadline, budget=budget)
    try:
        return result.stdout.decode("ascii")
    except UnicodeError:
        raise WatchdogError("INVALID_DOCKER_OUTPUT") from None


def absent(container: str, deadline: float, budget: OutputBudget) -> bool:
    """A successful exact-ID inventory, not an inspection error, proves absence."""
    if re.fullmatch(r"[0-9a-f]{64}", container) is None:
        raise WatchdogError("INVALID_CONTAINER_ID")
    lines = query(
        (
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--quiet",
            "--filter",
            f"id={container}",
        ),
        deadline,
        budget,
    ).splitlines()
    if lines not in ([], [container]):
        raise WatchdogError("INVALID_CONTAINER_INVENTORY")
    return lines == []


def verify_owner(
    container: str,
    owner: str,
    deadline: float,
    budget: OutputBudget,
    *,
    created: bool = False,
) -> None:
    if (
        re.fullmatch(r"[0-9a-f]{64}", container) is None
        or re.fullmatch(r"[0-9a-f]{32}", owner) is None
    ):
        raise WatchdogError("INVALID_OWNERSHIP_INPUT")
    try:
        details = json.loads(
            query(("container", "inspect", container), deadline, budget)
        )
        valid = (
            len(details) == 1
            and details[0]["Id"] == container
            and details[0]["Config"]["Labels"][LABEL] == owner
            and (not created or details[0]["State"]["Status"] == "created")
        )
    except (KeyError, TypeError, ValueError):
        raise WatchdogError("INVALID_OWNERSHIP_EVIDENCE") from None
    if not valid:
        raise WatchdogError("OWNERSHIP_OR_STATE_MISMATCH")


def remove_owned(
    container: str, owner: str, deadline: float, budget: OutputBudget
) -> str:
    if absent(container, deadline, budget):
        return "already_absent"
    verify_owner(container, owner, deadline, budget)
    query(("container", "rm", "--force", container), deadline, budget)
    if not absent(container, deadline, budget):
        raise WatchdogError("REMOVAL_NOT_VERIFIED")
    return "removed_and_absence_verified"


def _event(record: TextIO, event: dict[str, str | float | bool | int]) -> None:
    """Persist each bounded event before sending a best-effort pipe notification."""
    encoded = json.dumps(event, sort_keys=True, allow_nan=False) + "\n"
    if len(encoded.encode()) > 2048:
        raise WatchdogError("EVENT_LIMIT")
    record.write(encoded)
    record.flush()
    os.fsync(record.fileno())
    try:
        # Each event fits in a new pipe's atomic-write size. A disconnected
        # collector must not cancel the independent deadline action.
        os.write(1, encoded.encode())
    except BrokenPipeError:
        pass


def watch(
    container: str, owner: str, deadline: float, expected_boot: str, record_path: Path
) -> int:
    started = time.monotonic()
    if (
        re.fullmatch(r"[0-9a-f]{64}", container) is None
        or re.fullmatch(r"[0-9a-f]{32}", owner) is None
        or not math.isfinite(deadline)
        or not started < deadline <= started + MAX_SECONDS
        or expected_boot != boot_id()
    ):
        raise WatchdogError("INVALID_WATCHDOG_INPUT")
    parent = record_path.parent
    metadata = parent.stat()
    if (
        not record_path.is_absolute()
        or parent.is_symlink()
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise WatchdogError("UNSAFE_RECORD_DIRECTORY")
    # Exclusive creation prevents replacing an earlier run's evidence.
    descriptor = os.open(
        record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    budget = OutputBudget()
    with os.fdopen(descriptor, "w", encoding="ascii") as record:
        absence_verified = False
        try:
            verify_owner(container, owner, deadline, budget, created=True)
            if time.monotonic() >= deadline:
                raise WatchdogError("ARMING_DEADLINE_EXPIRED")
            _event(
                record,
                {
                    "event": "armed",
                    "container_id": container,
                    "owner": owner,
                    "deadline_monotonic": deadline,
                    "boot_id": expected_boot,
                    "watchdog_pid": os.getpid(),
                    "measurement_age": "unknown",
                    "approval_eligibility": "blocked",
                },
            )
            while time.monotonic() < deadline:
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            if boot_id() != expected_boot:
                raise WatchdogError("BOOT_ID_CHANGED")
            # Cleanup gets a separate bounded allowance, never a new live budget.
            cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
            outcome = remove_owned(container, owner, cleanup_deadline, budget)
            absence_verified = True
            if time.monotonic() > deadline + CLEANUP_SECONDS:
                raise WatchdogError("DEADLINE_ACTION_LATE")
            _event(
                record,
                {
                    "event": "finished",
                    "container_id": container,
                    "outcome": outcome,
                    "absence_verified": True,
                    "ended_monotonic": time.monotonic(),
                    "measurement_age": "unknown",
                    "approval_eligibility": "blocked",
                },
            )
            return 0
        except (CaptureError, WatchdogError) as error:
            _event(
                record,
                {
                    "event": "failed",
                    "container_id": container,
                    "reason_code": str(error),
                    "absence_verified": absence_verified,
                    "measurement_age": "unknown",
                    "approval_eligibility": "blocked",
                },
            )
            return 2
        except Exception:
            _event(
                record,
                {
                    "event": "failed",
                    "container_id": container,
                    "reason_code": "WATCHDOG_UNAVAILABLE",
                    "absence_verified": absence_verified,
                    "measurement_age": "unknown",
                    "approval_eligibility": "blocked",
                },
            )
            return 2


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    if len(args) != 6 or args[0] != "--watch":
        os.write(2, b"NTS_WATCHDOG=INVALID_INVOCATION\n")
        return 2
    try:
        return watch(args[1], args[2], float(args[3]), args[4], Path(args[5]))
    except Exception:
        os.write(2, b"NTS_WATCHDOG=FAILED\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
