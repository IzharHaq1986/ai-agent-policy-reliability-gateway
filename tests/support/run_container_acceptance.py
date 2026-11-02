"""Build the local API image and check required startup configuration."""

import json  # Inspect Docker state without printing container configuration.
import os  # Pass synthetic configuration through the subprocess environment.
import secrets  # Generate a disposable API credential.
import subprocess  # Run bounded Docker operations.
import tempfile  # Keep build and application output in private temporary storage.
import time  # Bound startup observation with a monotonic clock.
from http.client import HTTPConnection  # Send local HTTP without proxy settings.
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


def http_request(port, *, token=None, timeout=5):
    """Send one small request and bound response size and observation time."""
    deadline = time.monotonic() + timeout
    connection = HTTPConnection("127.0.0.1", port, timeout=timeout)
    payload = {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 42},
    }  # Use the existing supported policy request.
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        connection.connect()  # Establish direct loopback access without proxies.
        remaining = deadline - time.monotonic()
        require(remaining > 0, "HTTP connection deadline exceeded")
        connection.sock.settimeout(remaining)
        connection.request(
            "POST",
            "/v1/policy/evaluate",
            body=json.dumps(payload),
            headers=headers,
        )
        remaining = deadline - time.monotonic()
        require(remaining > 0, "HTTP request deadline exceeded")
        connection.sock.settimeout(remaining)
        response_socket = connection.sock
        response = connection.getresponse()
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, "HTTP response deadline exceeded")
            # Bound each read using the original connected socket.
            response_socket.settimeout(remaining)
            chunk = response.read1(4097 - len(body))
            if not chunk:
                break
            body.extend(chunk)
            require(len(body) <= 4096, "HTTP response exceeds acceptance limit")
        return response.status, json.loads(body)
    finally:
        connection.close()  # Release the socket on success and failure.


def check_http(image, container, network):
    """Exercise authentication and required audit failure through actual HTTP."""
    token = secrets.token_urlsafe(32)  # Use only a disposable test credential.
    conninfo = (
        "host=127.0.0.1 hostaddr=127.0.0.1 port=1 "
        "dbname=synthetic_unavailable user=synthetic_writer sslmode=disable"
    )  # Target only this container's loopback, never a real database.
    environment = os.environ.copy()
    environment["GATEWAY_API_TOKEN"] = token
    environment["GATEWAY_AUDIT_CONNINFO"] = conninfo

    docker("network", "create", "--driver", "bridge", network)
    docker(
        "create",
        "--name",
        container,
        "--pull",
        "never",
        "--platform",
        "linux/amd64",
        "--network",
        network,
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
        "--publish",
        "127.0.0.1::8000",
        "--env",
        "GATEWAY_API_TOKEN",
        "--env",
        "GATEWAY_AUDIT_CONNINFO",
        image,
        environment=environment,
    )  # Preserve the image's default audited startup command.
    docker("start", container)

    details = json.loads(docker("inspect", container))[0]
    host = details["HostConfig"]
    bindings = details["NetworkSettings"]["Ports"]["8000/tcp"]
    require(
        details["Config"]["User"] == "10001:10001"
        and host["ReadonlyRootfs"]
        and not host["Privileged"]
        and not host.get("CapAdd")
        and {value.upper() for value in host["CapDrop"]} == {"ALL"}
        and "no-new-privileges=true" in host["SecurityOpt"]
        and host["NanoCpus"] == 1_000_000_000
        and host["Memory"] == 256 * 1024 * 1024
        and host["PidsLimit"] == 128
        and not details["Mounts"]
        and set(details["NetworkSettings"]["Networks"]) == {network}
        and len(bindings) == 1
        and bindings[0]["HostIp"] == "127.0.0.1",
        "Unexpected HTTP container restrictions or publication",
    )
    port = int(bindings[0]["HostPort"])

    # Independently check the running identity and effective process restrictions.
    docker(
        "exec",
        container,
        "python",
        "-c",
        "import os; from pathlib import Path; "
        "assert (os.getuid(), os.getgid()) == (10001, 10001); "
        "s = dict(line.split(':', 1) for line in "
        "Path('/proc/1/status').read_text().splitlines() if ':' in line); "
        "assert int(s['NoNewPrivs'].strip()) == 1; "
        "assert all(int(s[k].strip(), 16) == 0 "
        "for k in ('CapEff', 'CapPrm', 'CapBnd'))",
    )
    print("CONTAINER_HTTP_RESTRICTIONS_AND_LOOPBACK=PASS")

    deadline = time.monotonic() + 25
    while True:
        state = json.loads(docker("inspect", container))[0]["State"]
        require(state["Running"], "HTTP container exited before availability")
        remaining = deadline - time.monotonic()
        require(remaining > 0, "HTTP container availability timed out")
        try:
            status, body = http_request(port, timeout=min(1, remaining))
        except OSError:
            # Repeat only unauthenticated probes while the server starts.
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            continue
        require(
            status == 401 and body == {"error": {"code": "UNAUTHENTICATED"}},
            "Unexpected unauthenticated HTTP response",
        )
        break
    print("CONTAINER_HTTP_AUTHENTICATION=PASS")

    # Verify the selected database endpoint refuses connections in this namespace.
    docker(
        "exec",
        container,
        "python",
        "-c",
        "import errno, socket; "
        "s = socket.socket(); s.settimeout(2); "
        "code = s.connect_ex(('127.0.0.1', 1)); s.close(); "
        "assert code == errno.ECONNREFUSED",
    )
    status, body = http_request(port, token=token, timeout=10)
    require(
        status == 500 and body == {"error": {"code": "INTERNAL_ERROR"}},
        "Audit connection refusal did not withhold the normal response",
    )  # Send the authenticated request exactly once; no fallback or retry.
    print("CONTAINER_HTTP_AUDIT_CONNECTION_REFUSAL=PASS")

    docker("stop", "--time", "5", container, timeout=15)
    state = json.loads(docker("inspect", container))[0]["State"]
    require(
        state["Status"] == "exited"
        and state["ExitCode"] == 0
        and not state["OOMKilled"]
        and not state["Error"],
        "HTTP container did not shut down gracefully",
    )
    print("CONTAINER_HTTP_GRACEFUL_SHUTDOWN=PASS")


def main():
    suffix = uuid4().hex  # Scope cleanup to resources owned by this invocation.
    image = f"gateway-container-acceptance:{suffix}"
    containers = [
        f"gateway-config-audit-{suffix}",
        f"gateway-config-token-{suffix}",
        f"gateway-http-{suffix}",
    ]
    network = f"gateway-http-network-{suffix}"
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
        stage = "container HTTP acceptance"
        check_http(image, containers[2], network)
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
            networks = [
                json.loads(line)
                for line in docker(
                    "network", "ls", "--format", "{{json .Name}}"
                ).splitlines()
            ]
            if network in networks:
                docker("network", "rm", network)
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
