"""Fixed harmless child for the inheritance proof; never a watchdog or tool."""

import errno  # Identify actual descriptor absence without magic numbers.
import json  # Send a small synthetic report through the inherited endpoint.
import math  # Reject an unbounded or invalid fixture deadline.
import os  # Check inherited descriptors and restore close-on-exec.
import select  # Wait within the parent-supplied synthetic deadline.
import socket  # Use the one endpoint explicitly passed by the trusted harness.
import sys  # Read only the fixed harness argument schema.
import time  # Keep the harmless fixture's lifetime bounded.
from pathlib import Path  # Read this child's own kernel start reference.

PACKET_BYTES = 2048  # Match the existing health protocol's packet ceiling.


def main() -> int:
    """Check inheritance, report it, and exit only after one fixed exit packet."""
    if len(sys.argv) != 7:  # Reject anything outside the fixed fixture interface.
        return 2  # Never reinterpret extra arguments as commands.
    # Use trusted FD references.
    endpoint_fd, parent_fd, sentinel_fd = map(int, sys.argv[1:4])
    deadline = float(sys.argv[4])  # Share the original synthetic startup deadline.
    mode = sys.argv[5]  # Failure tests select only reviewed harmless modes.
    expected_size = int(sys.argv[6])  # Detect packet-limit drift between the two files.
    if (
        mode not in {"normal", "early_exit", "stall"}  # No arbitrary action mode.
        or expected_size != PACKET_BYTES  # Reject drift from the shared packet ceiling.
        or not math.isfinite(deadline)  # Never wait on NaN or infinity.
        or not 0 < deadline - time.monotonic() <= 10  # Retain a bounded live budget.
        or min(endpoint_fd, parent_fd, sentinel_fd)
        <= 2  # Do not adopt standard streams.
        or len({endpoint_fd, parent_fd, sentinel_fd}) != 3  # Require distinct roles.
    ):
        return 2  # Invalid setup never produces a passing fixture report.
    if mode == "early_exit":  # Exercise child exit before any readiness evidence.
        return 3  # Let the parent's pidfd reveal this independent failure.
    # Adopt, rather than duplicate, our end.
    endpoint = socket.socket(fileno=endpoint_fd)
    try:  # Close our single owned endpoint on every ordinary exit path.
        endpoint.set_inheritable(False)  # Prevent later exec from retaining this end.
        endpoint.setblocking(False)  # Never let a socket operation wait indefinitely.
        # Check both deliberately excluded FDs.
        for unwanted in (
            parent_fd,
            sentinel_fd,
        ):
            try:  # A surviving excluded descriptor invalidates this fixture.
                # Observe kernel FD state instead of trusting arguments.
                os.fstat(unwanted)
            except OSError as error:  # Only actual absence is acceptable.
                # EBADF means this descriptor was closed at exec.
                if error.errno != errno.EBADF:
                    return 4  # Permission or other errors cannot prove absence.
            else:  # Any accessible excluded FD violates the tested allowlist.
                return 4  # Do not send a successful inheritance report.
        if mode == "normal":  # The stalled fixture deliberately emits no report.
            # Inspect our own lifetime.
            with Path("/proc/self/stat").open("rb") as source:
                raw = source.read(4097)  # Read one extra byte to detect oversized data.
            if len(raw) > 4096:  # Never parse an unbounded kernel observation.
                return 4  # Missing trustworthy lifetime data prevents this report.
            ticks = int(raw.rsplit(b") ", 1)[1].split()[19])  # Field 22 is start time.
            # Report only synthetic fixture observations.
            # Keep this report distinct from the health protocol.
            # Keep encoding deterministic and bounded.
            message = json.dumps(
                {
                    "version": 1,
                    # The parent independently verifies this assertion.
                    "pid": os.getpid(),
                    # Compare against the parent's bounded local read.
                    "start_ticks": ticks,
                    # Both explicitly excluded descriptors were absent.
                    "excluded_closed": True,
                    # Report restored ownership.
                    "non_inheritable": not endpoint.get_inheritable(),
                },
                sort_keys=True,
            ).encode("ascii")
            # Refuse to truncate any required report field.
            if len(message) > PACKET_BYTES:
                return 4  # A shortened report is not valid evidence.
            if endpoint.send(message, socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL) != len(
                message
            ):
                return 4  # A partial send cannot establish the tested handoff.
        # Wait without spinning while retaining a fixed deadline.
        poller = select.poll()
        # Observe our channel only.
        poller.register(endpoint, select.POLLIN | select.POLLHUP)
        # A stalled parent cannot keep this child forever.
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()  # Recompute the unchanged budget.
            if remaining <= 0:  # Avoid a negative or reset wait interval.
                break  # Expiry ends the harmless fixture.
            # Block only for the remainder.
            if not poller.poll(max(1, int(remaining * 1000))):
                continue  # Recheck the same deadline after an empty readiness result.
            try:  # Read only one complete fixed instruction from the trusted parent.
                packet, _, flags, _ = endpoint.recvmsg(
                    PACKET_BYTES, 0, socket.MSG_DONTWAIT
                )
            # Readiness can race; the original budget still applies.
            except BlockingIOError:
                continue  # No busy retry: the next iteration waits for readiness again.
            # Only this harmless instruction is accepted.
            if packet == b"exit" and not flags:
                return 0  # A normal exit is independently observed through the pidfd.
            return 4  # Closure, truncation, or unexpected packets fail the fixture.
        return 5  # Expiry is failure, never delayed success.
    finally:  # Release the endpoint whether reporting, waiting, or validation failed.
        # Closing this copy does not close a deliberately retained parent copy.
        endpoint.close()


if __name__ == "__main__":  # Imports do not start even this harmless fixture.
    # Convert invalid fixture inputs to a bounded failure status without a traceback.
    try:
        raise SystemExit(main())  # Return only the fixed fixture outcome.
    # Malformed setup cannot produce readiness.
    except (
        OSError,
        ValueError,
        IndexError,
    ):
        # Do not print private arguments or kernel contents.
        raise SystemExit(4) from None
