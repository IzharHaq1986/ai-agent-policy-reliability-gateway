"""Opt-in local Docker restart proof, never a live NTS experiment."""

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from nts_command_capture import OutputBudget, capture
from nts_container_watchdog import LABEL, absent, boot_id, remove_owned
from nts_workload_identity import (
    ExpectedWorkload,
    IdentityError,
    require_same,
    snapshot,
)
from run_container_acceptance import ROOT, docker, require
from run_nts_watchdog_acceptance import SLEEP, fixture


def observer(container, deadline, budget):
    """Read Docker and the kernel again without using snapshot's capture/parser."""
    data = json.loads(
        capture(
            ("docker", "inspect", container), deadline=deadline, budget=budget
        ).stdout
    )[0]
    require(
        data["Id"] == container and data["State"]["Running"],
        "Observer found no running workload",
    )
    pid = data["State"]["Pid"]
    require(type(pid) is int and 0 < pid < (1 << 31), "Invalid observer PID")
    require(
        time.monotonic() < deadline and budget.remaining > 0,
        "Observer budget exhausted",
    )
    room = min(4096, budget.remaining)
    with open(Path("/proc") / str(pid) / "stat", "rb") as stream:
        raw = stream.read(room + 1)
    budget._charge(len(raw))
    require(
        len(raw) <= room and time.monotonic() < deadline, "Observer stat exceeded limit"
    )
    ticks = int(raw.rpartition(b")")[2].split()[19])
    return pid, data["State"]["StartedAt"], data["RestartCount"], ticks


def run_case(image):
    owner = uuid4().hex
    container = None
    print(
        json.dumps(
            {
                "fixture_owner": owner,
                "fixture_name": "gateway-watchdog-fixture-" + owner,
            }
        ),
        flush=True,
    )
    try:
        container = fixture(image, owner)
        details = json.loads(
            capture(
                ("docker", "inspect", container),
                deadline=time.monotonic() + 2,
                budget=OutputBudget(),
            ).stdout
        )[0]
        host = details["HostConfig"]
        require(
            details["Image"] == image
            and details["Config"]["Cmd"] == SLEEP
            and details["Config"]["User"] == "10001:10001"
            and not details["Config"].get("Entrypoint")
            and host["NetworkMode"] == "none"
            and host["ReadonlyRootfs"]
            and not details["Mounts"]
            and not host.get("PortBindings")
            and not host["Privileged"]
            and host["CapDrop"] == ["ALL"]
            and "no-new-privileges=true" in host["SecurityOpt"]
            and host["NanoCpus"] == 1000000000
            and host["Memory"] == 134217728
            and host["PidsLimit"] == 32
            and host["RestartPolicy"]["Name"] == "no"
            and host["LogConfig"]
            == {"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
            "Unsafe identity fixture",
        )
        expected = ExpectedWorkload(container, image, owner, tuple(SLEEP), boot_id())
        deadline = time.monotonic() + 60
        budget = OutputBudget()
        docker("start", container)
        before = snapshot(expected, deadline=deadline, budget=budget)
        stable = snapshot(expected, deadline=deadline, budget=budget)
        require_same(expected, before, stable)
        observed_before = observer(container, deadline, budget)
        require(
            (
                stable.host_pid,
                stable.container_started_at,
                stable.restart_count,
                stable.process_start_ticks,
            )
            == observed_before,
            "First generation differs from independent observer",
        )
        print("NTS_IDENTITY_UNCHANGED_WORKLOAD=PASS")
        # Only this immutable owned fixture is restarted; never a host service.
        docker("restart", "--time", "1", container)
        after = snapshot(expected, deadline=deadline, budget=budget)
        observed_after = observer(container, deadline, budget)
        require(
            (
                after.host_pid,
                after.container_started_at,
                after.restart_count,
                after.process_start_ticks,
            )
            == observed_after,
            "Second generation differs from independent observer",
        )
        require(
            (observed_before[0], observed_before[1], observed_before[3])
            != (observed_after[0], observed_after[1], observed_after[3]),
            "Restart did not demonstrate a new generation",
        )
        try:
            require_same(expected, stable, after)
        except IdentityError as error:
            require(str(error) == "WORKLOAD_CHANGED", "Unexpected generation rejection")
        else:
            raise RuntimeError("Restarted workload matched its earlier generation")
        # This explicitly derived comparison is harder to reject: align just
        # the counter while retaining the independently observed new generation.
        counter_neutral = replace(after, restart_count=stable.restart_count)
        try:
            require_same(expected, stable, counter_neutral)
        except IdentityError as error:
            require(
                str(error) == "WORKLOAD_CHANGED",
                "Counter-independent check inconclusive",
            )
        else:
            raise RuntimeError("Identity comparison relied only on restart count")
        print(
            json.dumps(
                {
                    "container_id": container,
                    "image_id": image,
                    "observed_before": observed_before,
                    "observed_after": observed_after,
                    "counter_neutral_comparison": "rejected_derived_test_copy",
                    "measurement_age": "unknown",
                    "approval_eligibility": "blocked",
                },
                sort_keys=True,
            )
        )
        print("NTS_IDENTITY_SAME_CONTAINER_RESTART_REJECTED=PASS")
        print("NTS_IDENTITY_RESTART_COUNTER_INDEPENDENCE=PASS")
    finally:
        # Reconcile a lost create acknowledgement by owner, then act on full IDs.
        candidates = docker(
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--quiet",
            "--filter",
            f"label={LABEL}={owner}",
        ).splitlines()
        for candidate in candidates:
            remove_owned(candidate, owner, time.monotonic() + 10, OutputBudget())
        remaining = docker(
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--quiet",
            "--filter",
            f"label={LABEL}={owner}",
        ).strip()
        require(not remaining, "Identity fixture cleanup unverified")
        if container is not None:
            require(
                absent(container, time.monotonic() + 2, OutputBudget()),
                "Exact identity fixture remains",
            )
        print("NTS_IDENTITY_FIXTURE_CLEANUP=COMPLETE")


def main(arguments=None):
    args = sys.argv[1:] if arguments is None else arguments
    if args != ["--accept"]:
        print("Usage: run_nts_identity_acceptance.py --accept", file=sys.stderr)
        return 2
    # Fixture creation uses the existing helper's default endpoint. Require it
    # to be the same local default as the identity reader; never change context.
    require(
        not any(
            name in os.environ
            for name in (
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
            )
        ),
        "Docker endpoint override requires reconciliation",
    )
    require(
        docker("context", "show").strip() == "default",
        "Local default Docker context required",
    )
    image = "gateway-identity-acceptance:" + uuid4().hex
    attempted = False
    print(f"NTS_IDENTITY_FIXTURE_IMAGE={image}", flush=True)
    try:
        docker("buildx", "version")
        attempted = True
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
            require(result.returncode == 0, "Identity fixture build failed")
        image_id = json.loads(docker("image", "inspect", image))[0]["Id"]
        run_case(image_id)
    finally:
        if attempted:
            docker("image", "rm", "--force", image)
            require(
                not docker(
                    "image", "ls", "--quiet", "--filter", f"reference={image}"
                ).strip(),
                "Identity image cleanup unverified",
            )
            print("NTS_IDENTITY_IMAGE_CLEANUP=COMPLETE")
    print("NTS_IDENTITY_DOCKER_ACCEPTANCE=PASS")
    print("LIVE_NTS_COLLECTION=NOT_RUN")
    print("APPROVAL_ELIGIBILITY=BLOCKED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IdentityError as error:
        print("NTS_IDENTITY_FAILURE_REASON=" + str(error), file=sys.stderr)
        print("NTS_IDENTITY_DOCKER_ACCEPTANCE=FAILED", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("NTS_IDENTITY_DOCKER_ACCEPTANCE=FAILED", file=sys.stderr)
        raise SystemExit(2) from None
