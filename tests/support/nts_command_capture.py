"""Internal Linux command capture for a future NTS experiment, never a launcher."""

import math
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass

QUERY_SECONDS = 2.0
QUERY_BYTES = 32 * 1024
BUNDLE_BYTES = 1024 * 1024
CLEANUP_SECONDS = 1.0


class CaptureError(RuntimeError):
    """Expose only fixed failure codes, never arguments or captured output."""


class OutputBudget:
    """Account for bytes read across sequential queries, including failed queries."""

    def __init__(self, limit: int = BUNDLE_BYTES):
        if type(limit) is not int or not 0 < limit <= BUNDLE_BYTES:
            raise CaptureError("INVALID_OUTPUT_BUDGET")
        self._remaining = limit

    @property
    def remaining(self) -> int:
        return self._remaining

    def _charge(self, count: int) -> None:
        # One extra byte can be read to detect overflow, but never retained.
        self._remaining = max(0, self._remaining - count)


@dataclass(frozen=True)
class Captured:
    """Keep streams as bytes; the caller must validate their format separately."""

    stdout: bytes
    stderr: bytes
    started_monotonic: float
    ended_monotonic: float


def _cleanup(process: subprocess.Popen[bytes]) -> int:
    """Kill the owned group before reaping its leader, then close both pipes."""
    failed = False
    status = None
    try:
        # The leader is not reaped until after this signal, preventing PID reuse.
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        failed = True
    try:
        status = process.wait(timeout=CLEANUP_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        failed = True
    for stream in (process.stdout, process.stderr):
        if stream is None:
            failed = True
            continue
        try:
            stream.close()
        except OSError:
            failed = True
    if failed or status is None:
        raise CaptureError("PROCESS_CLEANUP_FAILED")
    return status


def _read(
    process: subprocess.Popen[bytes], deadline: float, budget: OutputBudget
) -> dict[str, bytearray]:
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    with selectors.DefaultSelector() as selector:
        for name in buffers:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CaptureError("QUERY_TIMEOUT")
            ready = selector.select(remaining)
            if not ready:
                raise CaptureError("QUERY_TIMEOUT")
            for key, _ in ready:
                if time.monotonic() >= deadline:
                    raise CaptureError("QUERY_TIMEOUT")
                available = min(QUERY_BYTES - total, budget.remaining)
                chunk = os.read(key.fd, min(4096, available + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                budget._charge(len(chunk))
                total += len(chunk)
                if len(chunk) > available:
                    raise CaptureError("OUTPUT_LIMIT")
                buffers[key.data].extend(chunk)
    # EOF is not proof of process exit. Observe without reaping so cleanup can
    # safely signal the original group even if descendants still exist.
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError("QUERY_TIMEOUT")
        state = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if state is not None:
            if state.si_code != os.CLD_EXITED or state.si_status != 0:
                raise CaptureError("COMMAND_FAILED")
            return buffers
        time.sleep(min(0.01, remaining))


def capture(
    command: tuple[str, ...], *, deadline: float, budget: OutputBudget
) -> Captured:
    """Run trusted argv with a shared deadline and budget; never use a shell."""
    if (
        not isinstance(command, tuple)
        or not command
        or any(not isinstance(arg, str) or "\0" in arg for arg in command)
        or not command[0]
        or type(deadline) not in (int, float)
        or not isinstance(budget, OutputBudget)
    ):
        raise CaptureError("INVALID_CAPTURE_INPUT")
    try:
        valid_deadline = math.isfinite(deadline)
    except OverflowError:
        valid_deadline = False
    if not valid_deadline:
        raise CaptureError("INVALID_DEADLINE")
    # A global child reaper could free the group leader ID before cleanup.
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise CaptureError("UNSAFE_CHILD_REAPER")
    started = time.monotonic()
    query_deadline = min(deadline, started + QUERY_SECONDS)
    if query_deadline <= started:
        raise CaptureError("QUERY_TIMEOUT")
    if budget.remaining <= 0:
        raise CaptureError("OUTPUT_BUDGET_EXHAUSTED")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            shell=False,
        )
    except FileNotFoundError:
        raise CaptureError("COMMAND_MISSING") from None
    except OSError:
        raise CaptureError("COMMAND_UNAVAILABLE") from None
    try:
        try:
            buffers = _read(process, query_deadline, budget)
        except (OSError, ValueError):
            raise CaptureError("CAPTURE_UNAVAILABLE") from None
    finally:
        # Also applies on KeyboardInterrupt and other interrupted reads. Cleanup
        # failure overrides success or the original failure with its fixed code.
        status = _cleanup(process)
    ended = time.monotonic()
    if ended >= query_deadline:
        raise CaptureError("QUERY_TIMEOUT")
    if status != 0:
        raise CaptureError("COMMAND_FAILED")
    return Captured(bytes(buffers["stdout"]), bytes(buffers["stderr"]), started, ended)
