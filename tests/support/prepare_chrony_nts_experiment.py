"""Build and inspect an isolated NTS experiment image without live time queries."""

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from run_container_acceptance import docker, require

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = ROOT / "tests/experiments/chrony_nts"
BASE = (
    "docker.io/library/ubuntu@sha256:"
    "78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d"
)
PACKAGE = "4.5-1ubuntu4.2"
CONFIG = (
    "server time.cloudflare.com iburst nts\n"
    "authselectmode require\n"
    "port 0\n"
    "cmdport 0\n"
    "bindcmdaddress /run/nts/chronyd.sock\n"
    "pidfile /run/nts/chronyd.pid\n"
    "driftfile /run/nts/drift\n"
)
ENTRYPOINT = [
    "/usr/sbin/chronyd",
    "-U",
    "-x",
    "-d",
    "-u",
    "ntslab",
    "-f",
    "/experiment/chrony.conf",
]
CHECK = """
printf '[VERSION]\\n'
/usr/sbin/chronyd --version
printf '[/VERSION]\\n[CONFIG]\\n'
/usr/sbin/chronyd -U -x -u ntslab -p -f /experiment/chrony.conf
printf '[/CONFIG]\\n[PACKAGES]\\n'
cat /experiment/package-versions.txt
printf '[/PACKAGES]\\n[HASH]\\n'
sha256sum /experiment/chrony.conf
printf '[/HASH]\\n[PROCESS]\\n'
id -u
id -g
grep -E '^(CapEff|NoNewPrivs):' /proc/self/status
printf '[/PROCESS]\\n'
"""


def section(output, name):
    """Require exactly one complete metadata section."""
    begin, end = f"[{name}]\n", f"[/{name}]\n"
    require(output.count(begin) == 1 and output.count(end) == 1, "Missing metadata")
    value = output.split(begin, 1)[1]
    require(end in value, "Invalid metadata order")
    return value.split(end, 1)[0].strip()


def validate_output(output):
    """Check the actual binary, package manifest, config, and process privileges."""
    require(len(output.encode()) <= 65536, "Preparation metadata exceeded limit")
    version = section(output, "VERSION")
    require(
        re.search(r"\bversion 4\.5\b", version) is not None
        and re.search(r"(?<!\S)\+NTS(?=[\s)])", version) is not None,
        "Expected chrony version with NTS support",
    )
    require(section(output, "CONFIG") != "", "Configuration was not parsed")
    packages = {}
    for line in section(output, "PACKAGES").splitlines():
        name, separator, value = line.partition("\t")
        require(
            bool(separator) and bool(name) and bool(value), "Invalid package manifest"
        )
        require(name not in packages, "Duplicate package manifest entry")
        packages[name] = value
    require(packages.get("chrony") == PACKAGE, "Unexpected chrony package")
    require(
        bool(packages.get("ca-certificates"))
        and bool(packages.get("libgnutls30t64:amd64")),
        "Missing certificate or TLS package evidence",
    )
    expected_hash = hashlib.sha256(CONFIG.encode()).hexdigest()
    require(
        section(output, "HASH").split() == [expected_hash, "/experiment/chrony.conf"],
        "Unexpected experiment configuration",
    )
    process = section(output, "PROCESS").splitlines()
    require(process[:2] == ["10001", "10001"], "Unexpected preparation user")
    fields = dict(line.split(":", 1) for line in process[2:])
    require(
        len(fields) == 2
        and int(fields["CapEff"].strip(), 16) == 0
        and fields["NoNewPrivs"].strip() == "1",
        "Unexpected effective process privileges",
    )
    return {
        "chrony_version": version,
        "packages": packages,
        "config_sha256": expected_hash,
    }


def prepare():
    """Preserve the prepared image and remove only the owned check container."""
    require((CONTEXT / "chrony.conf").read_text() == CONFIG, "Configuration differs")
    require(
        (CONTEXT / "Dockerfile").read_text().splitlines()[0] == f"FROM {BASE}",
        "Base image must match the pinned digest",
    )
    container = "gateway-nts-prepare-" + uuid4().hex
    attempted = False
    with tempfile.TemporaryDirectory(prefix="gateway-nts-prepare-") as temporary:
        image_file = Path(temporary) / "image-id"
        docker(
            "build",
            "--platform",
            "linux/amd64",
            "--iidfile",
            str(image_file),
            "--file",
            str(CONTEXT / "Dockerfile"),
            str(CONTEXT),
            timeout=600,
        )
        image = image_file.read_text().strip()
        require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None, "Invalid image ID"
        )
        details = json.loads(docker("image", "inspect", image))[0]
        require(
            details["Id"] == image
            and details["Architecture"] == "amd64"
            and details["Os"] == "linux"
            and details["Config"]["User"] == "10001:10001"
            and details["Config"]["Entrypoint"] == ENTRYPOINT,
            "Unexpected experiment image configuration",
        )
        try:
            attempted = True
            docker(
                "create",
                "--name",
                container,
                "--pull",
                "never",
                "--platform",
                "linux/amd64",
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
                "256m",
                "--pids-limit",
                "64",
                "--tmpfs",
                "/run/nts:rw,nosuid,nodev,noexec,size=16m,uid=10001,gid=10001,mode=0700",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=16m,uid=10001,gid=10001,mode=0700",
                "--entrypoint",
                "/bin/sh",
                image,
                "-ec",
                CHECK,
            )
            host = json.loads(docker("inspect", container))[0]["HostConfig"]
            require(
                host["NetworkMode"] == "none"
                and host["ReadonlyRootfs"]
                and host["CapDrop"] == ["ALL"]
                and not host["Privileged"]
                and host["NanoCpus"] == 1000000000
                and host["Memory"] == 268435456
                and host["PidsLimit"] == 64
                and not host.get("Binds")
                and not host.get("PortBindings"),
                "Unexpected preparation container isolation",
            )
            docker("start", container)
            require(
                docker("wait", container, timeout=20).strip() == "0",
                "Image check failed",
            )
            report = validate_output(docker("logs", container))
            report.update(
                image_id=image,
                base_image=BASE,
                endpoint="time.cloudflare.com",
                stage="prepared",
                live_nts_measurement="not_run",
                measurement_age="unknown",
                approval_eligibility="blocked",
            )
        finally:
            if attempted:
                # A cleanup failure propagates and prevents a preparation PASS.
                docker("rm", "--force", container)
    report["owned_container_cleanup"] = "complete"
    return report


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["--prepare"]:
        print("Usage: prepare_chrony_nts_experiment.py --prepare", file=sys.stderr)
        return 2
    try:
        report = prepare()
    except Exception:
        print("CHRONY_NTS_PREPARATION=FAILED", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    print("CHRONY_NTS_PREPARATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
