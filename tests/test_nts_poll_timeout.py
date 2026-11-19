"""Deterministic integer boundaries; no sleep, actual clock, socket, or poll call."""

import importlib  # Load only the reviewed pure test-support helper.
from pathlib import Path  # Locate the fixed helper directory without caller input.

import pytest  # Require exact rejection reasons rather than any exception.


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).parent / "support")
    )  # Fixed imports.
    return importlib.import_module("nts_poll_timeout")  # Import performs no waiting.


@pytest.mark.parametrize("origin", [0, 123456789, 2**63 - 1 - 3_000_000])
@pytest.mark.parametrize(
    "remaining,expected",
    [  # Exact expected poll-unit results.
        (1_000_000, 1),  # Minimum positive representable wait.
        (1_000_001, 1),  # A sub-millisecond remainder cannot add a poll unit.
        (1_999_999, 1),  # Almost two milliseconds still permits only one whole unit.
        (2_000_000, 2),  # Equality with the next whole unit is representable.
        (2_000_001, 2),  # Just past a boundary still rounds down.
    ],
)
def test_exact_relative_results_at_small_and_large_origins(
    module, origin, remaining, expected
):
    deadline = origin + remaining  # Keep both inputs inside the shared integer domain.
    actual = module.poll_timeout_ms(
        deadline, origin
    )  # Preserve the absolute input value.
    assert type(actual) is int and actual == expected  # No float or boolean can escape.
    assert (
        0 < actual * 1_000_000 <= remaining
    )  # Requested wait never extends the boundary.
    assert (
        remaining - actual * 1_000_000 < 1_000_000
    )  # Discard less than one poll unit.


@pytest.mark.parametrize("remaining", [1, 500_000, 999_999])
def test_positive_submillisecond_wait_is_not_repaired(module, remaining):
    with pytest.raises(module.PollTimeoutError, match="^WAIT_UNREPRESENTABLE$"):
        module.poll_timeout_ms(
            remaining, 0
        )  # Neither zero nor rounded-up one is allowed.


@pytest.mark.parametrize("deadline,now", [(1, 1), (50, 51), (1, 2**63 - 1)])
def test_equal_or_past_deadline_is_expired(module, deadline, now):
    with pytest.raises(module.PollTimeoutError, match="^DEADLINE_EXPIRED$"):
        module.poll_timeout_ms(
            deadline, now
        )  # Do not reinterpret expiry as a new wait.


@pytest.mark.parametrize("field", ["deadline", "now"])
@pytest.mark.parametrize("bad", [True, False, None, "1000000", 1.0, -1, 2**63, [], {}])
def test_invalid_inputs_are_rejected_without_coercion(module, field, bad):
    # Keep the other input valid so each rejection checks the selected boundary.
    deadline, now = (bad, 0) if field == "deadline" else (1_000_000, bad)
    with pytest.raises(module.PollTimeoutError, match="^INVALID_TIME_INPUT$"):
        module.poll_timeout_ms(
            deadline, now
        )  # Wrong types cannot select expiry semantics.


def test_zero_deadline_is_invalid_not_expired(module):
    with pytest.raises(module.PollTimeoutError, match="^INVALID_TIME_INPUT$"):
        module.poll_timeout_ms(
            0, 0
        )  # The shared lifetime requires a positive deadline.


@pytest.mark.parametrize("remainder", [0, 1, 999_999])
def test_largest_signed_poll_timeout_stays_positive(module, remainder):
    largest = (
        2_147_483_647  # Independent literal for the native signed timeout ceiling.
    )
    remaining = (
        largest * 1_000_000 + remainder
    )  # Fraction cannot increase the quotient.
    result = module.poll_timeout_ms(remaining, 0)  # No overflowing native call is made.
    assert result == largest  # Preserve the greatest supported positive timeout.
    assert (
        0 < result * 1_000_000 <= remaining
    )  # Check the safety inequality at the limit.


@pytest.mark.parametrize("deadline", [2_147_483_648_000_000, 2**63 - 1])
def test_poll_timeout_overflow_never_wraps_or_clamps(module, deadline):
    with pytest.raises(module.PollTimeoutError, match="^POLL_TIMEOUT_OVERFLOW$"):
        module.poll_timeout_ms(
            deadline, 0
        )  # Both timestamps are valid, but wait is not.


def test_explicit_integer_grid_never_extends_deadline(module):
    for milliseconds in (1, 2, 31, 1000, 10_000, 2_147_483_647):  # Fixed finite cases.
        for fraction in (
            0,
            1,
            499_999,
            999_999,
        ):  # Exercise exact and near-next boundaries.
            remaining = milliseconds * 1_000_000 + fraction  # No random or real time.
            result = module.poll_timeout_ms(
                remaining + 17, 17
            )  # Nonzero original domain.
            assert (
                0 < result * 1_000_000 <= remaining
            )  # Fundamental no-extension bound.
            assert (
                result + 1
            ) * 1_000_000 > remaining  # Result is the greatest safe unit.


def test_repeated_calls_do_not_create_lifecycle_or_clock_trust(module):
    assert (
        module.poll_timeout_ms(3_000_000, 1_000_000) == 2
    )  # First supplied observation.
    assert (
        module.poll_timeout_ms(3_000_000, 0) == 3
    )  # No retained monotonic history exists.
    assert (
        module.poll_timeout_ms(3_000_000, 1_000_000) == 2
    )  # Same inputs repeat identically.
    # Trusted orchestration must reject stale/backward observations across actual waits.
