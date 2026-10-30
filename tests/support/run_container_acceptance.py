"""Build the local API image and check required startup configuration."""

import json  # Inspect Docker state without printing container configuration.
import os  # Pass synthetic configuration through the subprocess environment.
import secrets  # Generate a disposable API credential.
import subprocess  # Run bounded Docker operations.
import tempfile  # Keep build and application output in private temporary storage.
from pathlib import Path  # Locate the repository independently of the caller.
from uuid import uuid4  # Give owned Docker resources independent names.

ROOT = Path(__file__).resolve().parents[2]  # Build from the repository root.
COMMAND = [  # Require the existing audited API entry point.
    "python",
    "-m",
    "uvicorn",
    "gateway.api_startup:create_postgresql_app",
    "--factory",
    "--app-dir",
    "/app/src",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
]


def docker(*arguments, timeout=30, environment=None):
    """Capture Docker output and expose only the failed operation name."""
    try:
        result = subprocess.run(
            ["docker", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError(f"Docker {arguments[0]} did not finish") from None
    if result.returncode:
        raise RuntimeError(f"Docker {arguments[0]} failed")
    return result.stdout


def require(condition, message):
    """Keep acceptance checks active even when Python assertions are disabled."""
    if not condition:
        raise RuntimeError(message)


def check_configuration(image, container, missing, expected):
    """Require the intended startup rejection, rather than an arbitrary crash."""
    environment = os.environ.copy()  # Preserve Docker client configuration.
    for name in ("GATEWAY_API_TOKEN", "GATEWAY_AUDIT_CONNINFO"):
        environment.pop(name, None)  # Exclude inherited application credentials.

    settings = (
        ["GATEWAY_API_TOKEN"]
        if missing == "audit_configuration"
        else ["GATEWAY_AUDIT_CONNINFO"]
    )
    if missing == "audit_configuration":
        environment["GATEWAY_API_TOKEN"] = secrets.token_urlsafe(32)
    else:
        environment["GATEWAY_AUDIT_CONNINFO"] = (
            "host=127.0.0.1 port=1 "
            "dbname=synthetic_unavailable user=synthetic_writer sslmode=disable"
        )  # Supply valid syntax without credentials or a real database.

    arguments = [
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
        "128",
    ]
    for setting in settings:
        arguments.extend(["--env", setting])  # Pass names, never secret values.
    arguments.append(image)  # Use the image's unchanged default startup command.
    docker(*arguments, environment=environment)
    docker("start", container)
    exit_text = docker("wait", container, timeout=30).strip()
    state = json.loads(docker("inspect", container))[0]["State"]
    require(
        exit_text == "1"
        and state["Status"] == "exited"
        and state["ExitCode"] == 1
        and not state["OOMKilled"]
        and not state["Error"],
        "Container did not exit through the expected startup failure",
    )
    # Read both streams once; Uvicorn normally writes errors to stderr.
    logs = subprocess.run(
        ["docker", "logs", container],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    output = logs.stdout + logs.stderr
    require(
        f"RuntimeError: {expected}" in output.splitlines(),
        "Expected startup configuration rejection missing",
    )
    print(f"CONTAINER_CONFIGURATION_CASE=missing_{missing}:PASS")


def main():
    suffix = uuid4().hex  # Scope cleanup to resources owned by this invocation.
    image = f"gateway-container-acceptance:{suffix}"
    containers = [
        f"gateway-config-audit-{suffix}",
        f"gateway-config-token-{suffix}",
    ]
    stage = "tool inspection"
    try:
        docker("buildx", "version")  # Fail rather than install or bypass tooling.
        with tempfile.TemporaryFile(mode="w+b") as capture:
            stage = "image build"
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
                stdout=capture,
                stderr=subprocess.STDOUT,
                timeout=600,
            )  # Keep raw build output private; the Dockerfile runs pip check.
            require(result.returncode == 0, "Container image build failed")

        stage = "image inspection"
        details = json.loads(docker("image", "inspect", image))[0]
        require(
            details["Os"] == "linux"
            and details["Architecture"] == "amd64"
            and details["Config"]["User"] == "10001:10001"
            and details["Config"]["Cmd"] == COMMAND
            and not details["Config"].get("Entrypoint"),
            "Unexpected container platform, identity, or startup command",
        )
        configured = details["Config"].get("Env") or []
        require(
            not any(
                value.split("=", 1)[0]
                in {"GATEWAY_API_TOKEN", "GATEWAY_AUDIT_CONNINFO"}
                for value in configured
            ),
            "Application configuration must not be embedded in the image",
        )
        print("CONTAINER_IMAGE_BUILD_AND_CONFIGURATION=PASS")

        for container, missing, expected in (
            (
                containers[0],
                "audit_configuration",
                "Invalid audit database configuration",
            ),
            (
                containers[1],
                "api_credential",
                "Invalid gateway credential configuration",
            ),
        ):
            stage = f"missing {missing}"
            check_configuration(image, container, missing, expected)
    except Exception:
        raise RuntimeError(f"Container acceptance failed during {stage}") from None
    finally:
        cleanup_failed = False  # Attempt every cleanup even after another fails.
        for container in containers:
            try:
                found = docker(
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"name=^/{container}$",
                ).strip()
                if found:
                    docker("rm", "--force", container)
            except Exception:
                cleanup_failed = True
        try:
            found = docker("image", "ls", "--quiet", image).strip()
            if found:
                docker("image", "rm", image)  # Remove only the unique local tag.
        except Exception:
            cleanup_failed = True
        if cleanup_failed:
            raise RuntimeError("Owned container acceptance resource cleanup failed")
        print("CONTAINER_ACCEPTANCE_RESOURCES=REMOVED")

    print("CONTAINER_CI_CONFIGURATION_ACCEPTANCE=PASS")


if __name__ == "__main__":
    main()  # Execute only when explicitly invoked.
