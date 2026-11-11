"""Verify bounded read-only collection and permanently blocked eligibility."""

import json
import subprocess
import sys

import pytest

from gateway import timesyncd_inspection as module

BOOT = "2ecf71e1-a994-4f80-bfb5-10f4f800e72f"
SERVICE = """Id=systemd-timesyncd.service
ActiveState=active
SubState=running
MainPID=678
InvocationID=2630fa3efcad45cf963034f8d96172a3
ExecMainStartTimestampMonotonic=6239114
"""
OWNER = 's ":1.42"\n'
SOURCE = 's "ntp.hetzner.com"\n'
PACKET = (
    "(uuuuittayttttbtt) 0 4 4 2 -23 10818 20263 4 237 17 204 95 "
    "1794402513896535 1794402513914433 1794402513914463 "
    "1794402513930839 false 10 2672\n"
)
COMMANDS = [
    module._SERVICE,
    module._OWNER,
    module._SOURCE,
    module._PACKET,
    module._SOURCE,
    module._OWNER,
    module._SERVICE,
]


def install_snapshot(monkeypatch, *, replacements=None, boots=(BOOT, BOOT)):
    """Use only fixed read queries and a separately controlled local timer."""
    outputs = [SERVICE, OWNER, SOURCE, PACKET, SOURCE, OWNER, SERVICE]
    for index, output in (replacements or {}).items():
        outputs[index] = output
    calls = []

    def query(command):
        index = len(calls)
        assert command == COMMANDS[index]
        calls.append(command)
        return outputs[index]

    boot_values = iter(boots)
    readings = iter((100, 200))
    monkeypatch.setattr(module, "_query", query)
    monkeypatch.setattr(module, "_boot_id", lambda: next(boot_values))
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: next(readings))
    return calls


def assert_blocked(report):
    assert report["approval_eligibility"] == "blocked"
    assert report["measurement_age"] == "unknown"
    assert report["source_trust"] == "unverified"


def test_complete_snapshot_retains_precision_and_blocks_eligibility(
    monkeypatch, capsys
):
    calls = install_snapshot(monkeypatch)
    assert module.main(["--inspect"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["collection_status"] == "complete"
    assert report["reason_code"] == "SNAPSHOT_COLLECTED"
    assert calls == COMMANDS
    assert_blocked(report)
    assert report["evidence"]["collection_duration_ns"] == 100
    assert report["evidence"]["server_name"] == "ntp.hetzner.com"
    assert report["evidence"]["packet"]["destination_timestamp_us"] == 1794402513930839
    assert report["evidence"]["packet"]["packet_count"] == 10


def test_repeated_cached_packet_still_has_unknown_age(monkeypatch):
    install_snapshot(monkeypatch)
    first = module.inspect_timesyncd()
    install_snapshot(monkeypatch)
    second = module.inspect_timesyncd()
    assert first["evidence"]["packet"] == second["evidence"]["packet"]
    assert first["collector_epoch"] != second["collector_epoch"]
    assert_blocked(first)
    assert_blocked(second)


@pytest.mark.parametrize(
    ("index", "value", "reason"),
    [
        (0, "unsupported", "MALFORMED_PACKET"),
        (1, "3", "UNHEALTHY_PACKET"),
        (2, "3", "UNHEALTHY_PACKET"),
        (3, "3", "UNHEALTHY_PACKET"),
        (4, "0", "UNHEALTHY_PACKET"),
        (5, "-129", "MALFORMED_PACKET"),
        (6, "-1", "MALFORMED_PACKET"),
        (8, "5", "MALFORMED_PACKET"),
        (9, "256", "MALFORMED_PACKET"),
        (13, "99999999999999999999", "MALFORMED_PACKET"),
        (14, "1794402513914464", "MALFORMED_PACKET"),
        (17, "true", "UNHEALTHY_PACKET"),
        (18, "0", "UNHEALTHY_PACKET"),
    ],
)
def test_invalid_packet_produces_unavailable_report(monkeypatch, index, value, reason):
    fields = PACKET.split()
    fields[index] = value
    install_snapshot(monkeypatch, replacements={3: " ".join(fields)})
    report = module.inspect_timesyncd()
    assert report["collection_status"] == "unavailable"
    assert report["reason_code"] == reason
    assert "evidence" not in report
    assert_blocked(report)


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (6, SERVICE.replace("MainPID=678", "MainPID=679")),
        (5, 's ":1.43"'),
        (4, 's "another.example"'),
    ],
)
def test_identity_changes_invalidate_snapshot(monkeypatch, index, value):
    install_snapshot(monkeypatch, replacements={index: value})
    report = module.inspect_timesyncd()
    assert report["reason_code"] == "IDENTITY_CHANGED"
    assert report["collection_status"] == "unavailable"
    assert_blocked(report)


def test_boot_change_invalidates_snapshot(monkeypatch):
    install_snapshot(monkeypatch, boots=(BOOT, "11111111-1111-4111-8111-111111111111"))
    assert module.inspect_timesyncd()["reason_code"] == "IDENTITY_CHANGED"


@pytest.mark.parametrize(
    ("index", "value", "reason"),
    [
        (0, SERVICE + "MainPID=678\n", "MALFORMED_SERVICE"),
        (0, SERVICE.replace("active\n", "inactive\n"), "SERVICE_UNAVAILABLE"),
        (1, 's "bad-owner"', "MALFORMED_BUS_TEXT"),
        (2, 's ""', "MALFORMED_BUS_TEXT"),
    ],
)
def test_unusable_fields_are_explicit(monkeypatch, index, value, reason):
    install_snapshot(monkeypatch, replacements={index: value})
    report = module.inspect_timesyncd()
    assert report["collection_status"] == "unavailable"
    assert report["reason_code"] == reason
    assert_blocked(report)


def test_unexpected_error_hides_raw_details(monkeypatch, capsys):
    def fail():
        raise OSError("private service details")

    monkeypatch.setattr(module, "_boot_id", fail)
    assert module.main(["--inspect"]) == 2
    captured = capsys.readouterr()
    assert "private service details" not in captured.out + captured.err
    report = json.loads(captured.out)
    assert report["reason_code"] == "INSPECTION_UNAVAILABLE"
    assert_blocked(report)


def test_explicit_opt_in_is_required(monkeypatch, capsys):
    def unexpected_collection():
        pytest.fail("Usage error started collection")

    monkeypatch.setattr(module, "inspect_timesyncd", unexpected_collection)
    assert module.main([]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["reason_code"] == "USAGE_ERROR"
    assert_blocked(report)


def test_output_failure_returns_error(monkeypatch):
    def fail_output(output):
        raise OSError("unwritable output")

    monkeypatch.setattr(module, "write_stdout", fail_output)
    assert module.main([]) == 2


def test_query_accepts_exact_output_limit():
    output = module._query(
        (sys.executable, "-c", "import os; os.write(1, b'x' * 8192)")
    )
    assert len(output) == 8192


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("import os; os.write(1, b'x' * 1000000)", "QUERY_OUTPUT_LIMIT"),
        ("print('private error'); raise SystemExit(1)", "QUERY_FAILED"),
        ("import os; os.write(1, b'\\xff')", "QUERY_UNAVAILABLE"),
    ],
)
def test_query_failures_are_bounded_and_sanitized(code, reason):
    with pytest.raises(module._Unavailable) as failure:
        module._query((sys.executable, "-c", code))
    assert str(failure.value) == reason


def test_query_timeout_kills_and_reaps_owned_process(monkeypatch):
    real_popen = subprocess.Popen
    processes = []

    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs.get("shell", False) is False
        return process

    monkeypatch.setattr(module, "_QUERY_SECONDS", 0.05)
    monkeypatch.setattr(module.subprocess, "Popen", start)
    with pytest.raises(module._Unavailable, match="QUERY_TIMEOUT"):
        module._query((sys.executable, "-c", "import time; time.sleep(30)"))
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_missing_command_is_explicit():
    with pytest.raises(module._Unavailable, match="COMMAND_MISSING"):
        module._query(("/missing-gateway-inspection-test-command",))


def test_denied_command_is_explicit(monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("private path details")

    monkeypatch.setattr(module.subprocess, "Popen", denied)
    with pytest.raises(module._Unavailable, match="QUERY_UNAVAILABLE"):
        module._query(module._SERVICE)
