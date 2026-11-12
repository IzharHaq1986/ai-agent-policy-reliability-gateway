"""Opt-in Docker proof of independent removal, without any live time queries."""

import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from nts_command_capture import CaptureError, OutputBudget, capture
from nts_container_watchdog import LABEL, absent, boot_id, remove_owned
from run_container_acceptance import ROOT, docker, require

SLEEP = ["python", "-c", "import time; time.sleep(600)"]


def ready(process, container, owner, deadline):
    """No container start is permitted without a bounded matching acknowledgement."""
    data = bytearray()
    acknowledgement_deadline = min(deadline, time.monotonic() + 2)
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while b"\n" not in data:
            remaining = acknowledgement_deadline - time.monotonic()
            require(remaining > 0 and selector.select(remaining), "Readiness timed out")
            chunk = os.read(process.stdout.fileno(), 2049 - len(data))
            require(bool(chunk), "Watchdog exited before readiness")
            data.extend(chunk)
            require(len(data) <= 2048, "Readiness output exceeded limit")
    event = json.loads(bytes(data))
    require(
        event["event"] == "armed"
        and event["owner"] == owner
        and event["container_id"] == container
        and event["watchdog_pid"] == process.pid
        and event["deadline_monotonic"] == deadline
        and event["boot_id"] == boot_id()
        and time.monotonic() < deadline,
        "Invalid watchdog readiness",
    )


def fixture(image, owner):
    container = docker(
        "create",
        "--pull",
        "never",
        "--platform",
        "linux/amd64",
        "--name",
        "gateway-watchdog-fixture-" + owner,
        "--label",
        f"{LABEL}={owner}",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--cpus",
        "1",
        "--memory",
        "128m",
        "--pids-limit",
        "32",
        "--log-driver",
        "json-file",
        "--log-opt",
        "max-size=1m",
        "--log-opt",
        "max-file=1",
        "--restart",
        "no",
        image,
        *SLEEP,
    ).strip()
    return container


def run_case(image, case):
    with tempfile.TemporaryDirectory(prefix="gateway-watchdog-evidence-") as directory:
        _run_case(image, case, Path(directory) / "watchdog.jsonl")


def _run_case(image, case, record):
    print(f"NTS_WATCHDOG_CASE={case}:START")
    passed = False
    owner = uuid4().hex
    container = None
    process = None
    print(
        json.dumps(
            {
                "case": case,
                "fixture_owner": owner,
                "fixture_name": "gateway-watchdog-fixture-" + owner,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        container = fixture(image, owner)
        details = json.loads(docker("inspect", container))[0]
        host = details["HostConfig"]
        require(
            details["Id"] == container
            and details["Config"]["User"] == "10001:10001"
            and details["Config"]["Cmd"] == SLEEP
            and not details["Config"].get("Entrypoint")
            and details["Config"]["Labels"][LABEL] == owner
            and host["NetworkMode"] == "none"
            and host["ReadonlyRootfs"]
            and host["CapDrop"] == ["ALL"]
            and not host["Privileged"]
            and "no-new-privileges=true" in host["SecurityOpt"]
            and host["NanoCpus"] == 1000000000
            and host["Memory"] == 134217728
            and host["PidsLimit"] == 32
            and host["RestartPolicy"]["Name"] == "no"
            and host["LogConfig"]
            == {"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}}
            and not details["Mounts"]
            and not host.get("PortBindings"),
            "Unsafe synthetic fixture",
        )
        deadline = time.monotonic() + 8
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests/support/nts_container_watchdog.py"),
                "--watch",
                container,
                owner,
                repr(deadline),
                boot_id(),
                str(record),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ready(process, container, owner, deadline)
        require(process.poll() is None, "Watchdog exited after arming")
        if case != "created":
            capture(
                ("docker", "start", container),
                deadline=deadline,
                budget=OutputBudget(),
            )
            require(
                json.loads(docker("inspect", container))[0]["State"]["Running"],
                "Fixture never ran",
            )
        if case == "disconnected":
            process.stdout.close()
        # Deliberately block this collector until after the deadline. The
        # watchdog must act without requests or progress from this process.
        time.sleep(max(0, deadline - time.monotonic()) + 11)
        resumed = time.monotonic()
        require(process.wait(timeout=12) == 0, "Watchdog did not finish successfully")
        require(record.stat().st_size <= 4096, "Oversize watchdog evidence")
        events = [json.loads(line) for line in record.read_text().splitlines()]
        require(
            len(events) == 2
            and events[1]["event"] == "finished"
            and events[1]["outcome"] == "removed_and_absence_verified"
            and events[1]["ended_monotonic"] >= deadline
            and events[1]["ended_monotonic"] < resumed
            and events[1]["absence_verified"] is True,
            "Deadline removal was not proved during collector blocking",
        )
        require(
            absent(container, time.monotonic() + 2, OutputBudget()),
            "Independent absence check failed",
        )
        if case == "created":
            try:
                capture(
                    ("docker", "start", container),
                    deadline=time.monotonic() + 2,
                    budget=OutputBudget(),
                )
            except CaptureError as error:
                require(
                    str(error) == "COMMAND_FAILED",
                    "Late-start result inconclusive",
                )
            else:
                raise RuntimeError("Removed fixture unexpectedly started")
            require(
                absent(container, time.monotonic() + 2, OutputBudget()),
                "Late start changed absence",
            )
        print(
            json.dumps(
                {
                    "case": case,
                    "container_id": container,
                    "deadline_monotonic": deadline,
                    "watchdog_finished_monotonic": events[1]["ended_monotonic"],
                    "collector_resumed_monotonic": resumed,
                    "absence_verified": True,
                    "watchdog_events": events,
                },
                sort_keys=True,
            )
        )
        print(f"NTS_WATCHDOG_CASE={case}:PASS")
        passed = True
    finally:
        cleanup_failed = False
        try:
            # Reconcile lost creation acknowledgement through the unique label;
            # resolve an immutable ID and verify ownership before each removal.
            candidates = (
                capture(
                    (
                        "docker",
                        "container",
                        "ls",
                        "--all",
                        "--no-trunc",
                        "--quiet",
                        "--filter",
                        f"label={LABEL}={owner}",
                    ),
                    deadline=time.monotonic() + 2,
                    budget=OutputBudget(),
                )
                .stdout.decode("ascii")
                .splitlines()
            )
            for candidate in candidates:
                remove_owned(candidate, owner, time.monotonic() + 10, OutputBudget())
            remaining = capture(
                (
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--no-trunc",
                    "--quiet",
                    "--filter",
                    f"label={LABEL}={owner}",
                ),
                deadline=time.monotonic() + 2,
                budget=OutputBudget(),
            ).stdout.strip()
            require(not remaining, "Fixture cleanup unverified")
        except Exception:
            cleanup_failed = True
        if process is not None:
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
                cleanup_failed = True
            finally:
                if process.stdout is not None:
                    process.stdout.close()
        if record.exists() and not passed:
            try:
                with record.open("rb") as evidence:
                    raw = evidence.read(4097)
                require(len(raw) <= 4096, "Oversize failure evidence")
                events = [json.loads(line) for line in raw.splitlines()]
                print(
                    json.dumps(
                        {"case": case, "watchdog_events": events}, sort_keys=True
                    )
                )
            except Exception:
                cleanup_failed = True
        require(not cleanup_failed, "Fixture or watchdog process cleanup failed")


def main(arguments=None):
    args = sys.argv[1:] if arguments is None else arguments
    if args != ["--accept"]:
        print("Usage: run_nts_watchdog_acceptance.py --accept", file=sys.stderr)
        return 2
    image = "gateway-watchdog-acceptance:" + uuid4().hex
    attempted = False
    print(f"NTS_WATCHDOG_FIXTURE_IMAGE={image}", flush=True)
    try:
        docker("buildx", "version")
        attempted = True
        # Reuse the gateway image build without changing its default entrypoint.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--load",
                    "--tag",
                    image,
                    ".",
                ],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
            require(result.returncode == 0, "Fixture image build failed")
        image_id = json.loads(docker("image", "inspect", image))[0]["Id"]
        for case in ("running", "created", "disconnected"):
            run_case(image_id, case)
    finally:
        if attempted:
            # Remove only the invocation's unique image tag, never prune images.
            docker("image", "rm", "--force", image)
            remaining = docker(
                "image", "ls", "--quiet", "--filter", f"reference={image}"
            ).strip()
            require(not remaining, "Fixture image cleanup unverified")
            print("NTS_WATCHDOG_IMAGE_CLEANUP=COMPLETE")
    print("NTS_WATCHDOG_DOCKER_ACCEPTANCE=PASS")
    print("LIVE_NTS_COLLECTION=NOT_RUN")
    print("APPROVAL_ELIGIBILITY=BLOCKED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("NTS_WATCHDOG_DOCKER_ACCEPTANCE=FAILED", file=sys.stderr)
        raise SystemExit(2) from None
