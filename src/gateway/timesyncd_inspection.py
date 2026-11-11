"""Opt-in, bounded inspection of local timesyncd evidence with eligibility blocked."""

import json
import os
import re
import selectors
import shlex
import subprocess
import sys
import time
from uuid import uuid4

from gateway.cli_output import write_stdout

_QUERY_SECONDS = 2.0
_MAX_OUTPUT_BYTES = 8192
_SERVICE_KEYS = {
    "Id",
    "ActiveState",
    "SubState",
    "MainPID",
    "InvocationID",
    "ExecMainStartTimestampMonotonic",
}
_SERVICE = (
    "/usr/bin/systemctl",
    "--no-pager",
    "--no-ask-password",
    "show",
    "systemd-timesyncd.service",
    *("--property=" + key for key in sorted(_SERVICE_KEYS)),
)
_BUS = ("/usr/bin/busctl", "--system", "--no-pager", "--auto-start=no")
_OWNER = (
    *_BUS,
    "call",
    "org.freedesktop.DBus",
    "/org/freedesktop/DBus",
    "org.freedesktop.DBus",
    "GetNameOwner",
    "s",
    "org.freedesktop.timesync1",
)
_PROPERTY = (
    *_BUS,
    "get-property",
    "org.freedesktop.timesync1",
    "/org/freedesktop/timesync1",
    "org.freedesktop.timesync1.Manager",
)
_SOURCE = (*_PROPERTY, "ServerName")
_PACKET = (*_PROPERTY, "NTPMessage")
_SIGNATURE = "(uuuuittayttttbtt)"


class _Unavailable(ValueError):
    """Carry a fixed reason code without command output or exception details."""


def _query(command: tuple[str, ...]) -> str:
    """Read at most the byte limit and kill an owned query when its deadline expires."""
    deadline = time.monotonic() + _QUERY_SECONDS
    try:
        with subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        ) as process:
            assert process.stdout is not None
            try:
                output = bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise _Unavailable("QUERY_TIMEOUT")
                        chunk = os.read(
                            process.stdout.fileno(),
                            min(4096, _MAX_OUTPUT_BYTES + 1 - len(output)),
                        )
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > _MAX_OUTPUT_BYTES:
                            raise _Unavailable("QUERY_OUTPUT_LIMIT")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Unavailable("QUERY_TIMEOUT")
                status = process.wait(timeout=remaining)
                if status != 0:
                    raise _Unavailable("QUERY_FAILED")
                return output.decode("ascii")
            finally:
                # Reap only the process created by this query, including failures.
                if process.poll() is None:
                    process.kill()
    except FileNotFoundError:
        raise _Unavailable("COMMAND_MISSING") from None
    except subprocess.TimeoutExpired:
        raise _Unavailable("QUERY_TIMEOUT") from None
    except (OSError, UnicodeError):
        raise _Unavailable("QUERY_UNAVAILABLE") from None


def _boot_id() -> str:
    """Bound the read of the fixed local boot-identity file."""
    with open("/proc/sys/kernel/random/boot_id", "rb") as stream:
        value = stream.read(65).decode("ascii").removesuffix("\n")
    if re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is None:
        raise _Unavailable("MALFORMED_BOOT_ID")
    return value


def _service_identity(output: str) -> dict[str, str]:
    """Require the exact requested fields from one active service."""
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in _SERVICE_KEYS or key in values:
            raise _Unavailable("MALFORMED_SERVICE")
        values[key] = value
    if values.keys() != _SERVICE_KEYS:
        raise _Unavailable("MALFORMED_SERVICE")
    if (
        values["Id"] != "systemd-timesyncd.service"
        or values["ActiveState"] != "active"
        or values["SubState"] != "running"
        or re.fullmatch(r"[0-9a-f]{32}", values["InvocationID"]) is None
        or values["InvocationID"] == "0" * 32
        or re.fullmatch(r"[1-9][0-9]{0,9}", values["MainPID"]) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", values["ExecMainStartTimestampMonotonic"])
        is None
    ):
        raise _Unavailable("SERVICE_UNAVAILABLE")
    return values


def _bus_text(output: str, pattern: str) -> str:
    """Parse a single bounded D-Bus string without accepting arbitrary text."""
    fields = shlex.split(output)
    if len(fields) != 2 or fields[0] != "s" or re.fullmatch(pattern, fields[1]) is None:
        raise _Unavailable("MALFORMED_BUS_TEXT")
    return fields[1]


def _packet(output: str) -> dict[str, object]:
    """Validate the observed interface profile and retain raw microsecond timestamps."""
    fields = output.split()
    if len(fields) != 20 or fields[0] != _SIGNATURE or fields[8] != "4":
        raise _Unavailable("MALFORMED_PACKET")
    numeric_indices = (*range(1, 8), *range(9, 17), 18, 19)
    if any(
        re.fullmatch(
            r"(0|-?[1-9][0-9]{0,19})" if i == 5 else r"(0|[1-9][0-9]{0,19})",
            fields[i],
        )
        is None
        for i in numeric_indices
    ):
        raise _Unavailable("MALFORMED_PACKET")
    numbers = {i: int(fields[i]) for i in numeric_indices}
    if (
        any(not 0 <= numbers[i] < 2**32 for i in range(1, 5))
        or not -128 <= numbers[5] <= 127
        or any(not 0 <= numbers[i] < 2**64 for i in (6, 7, 18, 19))
        or any(not 0 <= numbers[i] <= 255 for i in range(9, 13))
        or any(not 0 < numbers[i] <= 253402300799999999 for i in range(13, 17))
        or numbers[13] > numbers[16]
        or numbers[14] > numbers[15]
        or fields[17] not in {"true", "false"}
    ):
        raise _Unavailable("MALFORMED_PACKET")
    if (
        [numbers[i] for i in range(1, 4)] != [0, 4, 4]
        or not 1 <= numbers[4] <= 15
        or fields[17] != "false"
        or numbers[18] == 0
    ):
        raise _Unavailable("UNHEALTHY_PACKET")
    return {
        "leap": numbers[1],
        "version": numbers[2],
        "mode": numbers[3],
        "stratum": numbers[4],
        "precision_log2_seconds": numbers[5],
        "root_delay_us": numbers[6],
        "root_dispersion_us": numbers[7],
        "reference_id_bytes": [numbers[i] for i in range(9, 13)],
        "origin_timestamp_us": numbers[13],
        "receive_timestamp_us": numbers[14],
        "transmit_timestamp_us": numbers[15],
        "destination_timestamp_us": numbers[16],
        "ignored": False,
        "packet_count": numbers[18],
        "jitter_us": numbers[19],
    }


def inspect_timesyncd() -> dict[str, object]:
    """Collect one diagnostic baseline. No result authorizes approval creation."""
    report: dict[str, object] = {
        "schema_version": 1,
        "collection_status": "unavailable",
        "collector_epoch": str(uuid4()),
        "measurement_age": "unknown",
        "source_trust": "unverified",
        "approval_eligibility": "blocked",
    }
    try:
        started = time.monotonic_ns()
        boot = _boot_id()
        service = _service_identity(_query(_SERVICE))
        owner = _bus_text(_query(_OWNER), r":[0-9]{1,20}\.[0-9]{1,20}")
        source = _bus_text(_query(_SOURCE), r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}")
        packet = _packet(_query(_PACKET))
        source_after = _bus_text(_query(_SOURCE), r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}")
        owner_after = _bus_text(_query(_OWNER), r":[0-9]{1,20}\.[0-9]{1,20}")
        service_after = _service_identity(_query(_SERVICE))
        boot_after = _boot_id()
        ended = time.monotonic_ns()
        if (boot, service, owner, source) != (
            boot_after,
            service_after,
            owner_after,
            source_after,
        ):
            raise _Unavailable("IDENTITY_CHANGED")
        if ended < started:
            raise _Unavailable("COLLECTION_CLOCK_ANOMALY")
        report.update(
            collection_status="complete",
            reason_code="SNAPSHOT_COLLECTED",
            evidence={
                "boot_id": boot,
                "service": service,
                "bus_owner": owner,
                "server_name": source,
                "packet": packet,
                "collection_started_monotonic_ns": started,
                "collection_ended_monotonic_ns": ended,
                "collection_duration_ns": ended - started,
            },
        )
    except _Unavailable as failure:
        report["reason_code"] = str(failure)
    except Exception:
        # Hide raw subprocess, filesystem, and parser errors in diagnostic output.
        report["reason_code"] = "INSPECTION_UNAVAILABLE"
    return report


def main(argv: list[str] | None = None) -> int:
    """Require explicit inspection and emit one bounded JSON report."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--inspect"]:
        report = inspect_timesyncd()
    else:
        report = {
            "schema_version": 1,
            "collection_status": "unavailable",
            "reason_code": "USAGE_ERROR",
            "measurement_age": "unknown",
            "source_trust": "unverified",
            "approval_eligibility": "blocked",
        }
    try:
        write_stdout(json.dumps(report, sort_keys=True, allow_nan=False) + "\n")
    except OSError:
        sys.stderr.write("OUTPUT_ERROR\n")
        return 2
    return 0 if report["collection_status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
