"""Fixed harmless child using checked transport adoption; never a watchdog."""

import errno  # Distinguish an absent excluded descriptor from unavailable evidence.
import json  # Encode the bounded fixture report and deliberate bad health replies.
import math  # Reject invalid or unbounded synthetic startup deadlines.
import os  # Close only the raw inherited original owned by this child.
import select  # Wait within the original budget without spinning on an empty queue.
import sys  # Accept only the fixed harness schema and reviewed import location.
import time  # Preserve the parent's original startup deadline.
from pathlib import Path  # Resolve the reviewed support directory and own kernel data.


class FixtureReleaseError(RuntimeError):
    """Use a distinct exit status when a resource release was not confirmed."""


def main() -> int:
    """Adopt, release the original, report, answer one challenge, and exit."""
    if (
        len(sys.argv) != 13
    ):  # Old or malformed argument layouts cannot run this fixture.
        return 2  # Do not reinterpret additional fields as commands or import paths.
    # Isolated Python deliberately excludes the script directory from imports.
    original_path = sys.path[:]  # Restore interpreter search state after fixed imports.
    sys.path.insert(
        0, str(Path(__file__).resolve().parent)
    )  # Add only reviewed support code.
    try:  # No argument or environment variable can select either module's location.
        from nts_health_protocol import (
            PACKET_BYTES,
            HealthError,  # Malformed or mismatched challenges cannot pass.
            Lifetime,  # Build fixed expected context independently of the challenge.
            respond,  # Reuse the reviewed response construction and validation.
        )  # Reuse the existing packet ceiling.

        # Use the actual checked transport operations.
        from nts_health_transport import (
            # Track the adopted duplicate independently from the raw original.
            Endpoint,
            # Treat validation and close uncertainty as fixture failure.
            TransportError,
            # Permit only bounded readiness waiting under the original budget.
            WouldBlock,
            # Validate a duplicate before accepting this inherited endpoint.
            adopt_inherited,
        )
    # Avoid retaining an extra import-path entry, including in direct unit tests.
    finally:
        sys.path[:] = original_path  # Never accept a caller-selected import fallback.
    source_fd, parent_fd, sentinel_fd = map(
        int, sys.argv[1:4]
    )  # Fixed trusted FD references.
    deadline = float(
        sys.argv[4]
    )  # Use the unchanged parent-generated monotonic deadline.
    mode = sys.argv[5]  # Only reviewed harmless failure modes are accepted.
    expected_size = int(sys.argv[6])  # Check agreement with the shared packet limit.
    device, inode, buffer_bytes, ceiling = map(
        int, sys.argv[7:11]
    )  # Trusted launch context.
    expected_boot, collection_id = sys.argv[11:13]  # Parent-owned fixed launch context.
    if (  # Reject malformed context before treating any descriptor as owned input.
        mode
        not in {
            "normal",
            "early_exit",
            "stall",
            "source_close_failure",
            "retain_original",
            "health_close",  # Exit after the challenge without providing an answer.
            "health_lifetime",  # A reply with another lifetime cannot pass.
            "health_generation",  # A future generation timestamp must fail.
            "health_malformed",  # Emit a deliberately malformed response for tests.
            "health_mismatch",  # Emit a response with the wrong challenge token.
            "health_silent",  # Consume the challenge without answering it.
            "health_duplicate",  # Queue a forbidden extra response for tests.
        }
        or expected_size != PACKET_BYTES  # No packet-limit override is accepted.
        or not math.isfinite(deadline)  # Never wait on an infinite or NaN deadline.
        or not 0
        < deadline - time.monotonic()
        <= 10  # Retain an explicit finite lifetime.
        or min(source_fd, parent_fd, sentinel_fd) <= 2  # Do not adopt standard streams.
        or len({source_fd, parent_fd, sentinel_fd})
        != 3  # Require separate original roles.
    ):
        return 2  # Invalid setup cannot yield a successful fixture report.
    if (
        mode == "early_exit"
    ):  # Preserve the existing exit-before-readiness negative case.
        return (
            3  # Process exit releases inherited descriptors without a passing report.
        )
    source_open = (
        True  # This child owns the inherited original until its one close attempt.
    )
    endpoint: Endpoint | None = (
        None  # The adoption helper owns only a separate duplicate.
    )
    try:  # Every acquired reference has an explicit cleanup path.
        for excluded in (
            parent_fd,
            sentinel_fd,
        ):  # Check exclusions before FD numbers are reused.
            try:  # Kernel absence is stronger than a report assertion alone.
                os.fstat(
                    excluded
                )  # Do not assume close_fds worked merely from launch options.
            except (
                OSError
            ) as error:  # Only EBADF proves this original entry was absent.
                if (
                    error.errno != errno.EBADF
                ):  # Other failures make evidence unavailable.
                    return 4  # Do not report successful confinement from an error.
            else:  # A surviving excluded descriptor violates the fixed launch profile.
                return 4  # Adoption cannot repair a leaked unrelated reference.
        # Expected identity comes from the parent, not our reply.
        endpoint = adopt_inherited(
            source_fd,  # Borrow the original without transferring its close ownership.
            # Bind to the parent's captured kernel device identity.
            expected_device=device,
            expected_inode=inode,  # Bind to the parent's captured socket inode.
            buffer_bytes=buffer_bytes,  # Preserve explicit existing resource bounds.
            # Do not resize an invalid inherited socket.
            effective_buffer_limit=ceiling,
        )
        # Only a negative fixture deliberately retains the inherited original.
        if mode != "retain_original":
            source_open = False  # Never retry an uncertain close against a reused slot.
            try:  # Adoption left this original for the trusted child to release once.
                os.close(source_fd)  # Release only this child's inherited original.
            except OSError:  # An error cannot prove that the close did not occur.
                raise FixtureReleaseError("ORIGINAL_RELEASE_UNCONFIRMED") from None
            if (
                mode == "source_close_failure"
            ):  # Model lost confirmation after real close.
                raise FixtureReleaseError("ORIGINAL_RELEASE_UNCONFIRMED")
        owned_fd = (
            endpoint._owned().fileno()
        )  # Narrow synthetic inspection, not a handoff API.
        with Path("/proc/self/stat").open(
            "rb"
        ) as source:  # Observe this child's lifetime.
            raw = source.read(4097)  # Detect overflow before parsing kernel data.
        if len(raw) > 4096:  # Do not parse shortened or unbounded evidence.
            return 4  # Missing lifetime evidence prevents a passing report.
        ticks = int(
            raw.rsplit(b") ", 1)[1].split()[19]
        )  # Kernel field 22 is start time.
        if mode in {"normal", "retain_original"} or mode.startswith(
            "health_"
        ):  # A stalled fixture emits no report.
            message = json.dumps(
                {  # This versioned fixture report is not a health reply.
                    # Distinguish checked-copy ownership from the earlier raw proof.
                    "version": 2,
                    # The parent independently checks its retained child.
                    "pid": os.getpid(),
                    # The parent compares an independent kernel witness.
                    "start_ticks": ticks,
                    # The excluded originals were absent before adoption.
                    "excluded_closed": True,
                    # Verify the copy.
                    "non_inheritable": not endpoint._owned().get_inheritable(),
                    # This is only an untrusted locator for parent inspection.
                    "owned_fd": owned_fd,
                    # The retained-original negative case deliberately makes a false
                    # claim.
                    "original_closed": True,
                },
                sort_keys=True,
            ).encode("ascii")  # Keep encoding deterministic and bounded.
            endpoint.send(
                message
            )  # Reuse whole-packet limits and explicit transport failures.
        health_answered = (
            False  # This fixture accepts exactly one challenge before exit.
        )
        # Do not derive expected lifetime fields from a wire packet.
        lifetime = Lifetime(
            # Parent launch context, not self-reported authority.
            boot_id=expected_boot,
            collection_id=collection_id,  # Parent-generated identity for this attempt.
            # Explicit synthetic placeholder; there is no container.
            container_id="0" * 64,
            owner="0" * 32,  # Explicit synthetic placeholder; no ownership attestation.
            watchdog_pid=os.getpid(),  # The fixed child checks its own actual process.
            watchdog_start_ticks=ticks,  # Actual reference, also in stalled tests.
            deadline_ns=int(
                deadline * 1_000_000_000
            ),  # Preserve the original boundary.
        )
        poller = select.poll()  # Wait for input instead of repeatedly retrying receive.
        poller.register(
            owned_fd, select.POLLIN | select.POLLHUP
        )  # Observe only our owned copy.
        while (
            time.monotonic() < deadline
        ):  # A silent parent cannot keep this fixture forever.
            remaining = (
                deadline - time.monotonic()
            )  # Never replace the original deadline.
            if remaining <= 0:  # Stop before issuing an expired wait.
                break  # Expiry is failure, not delayed success.
            if not poller.poll(
                max(1, math.ceil(remaining * 1000))
            ):  # Wait only for the remainder.
                continue  # Recheck the same budget after an empty readiness result.
            if (
                time.monotonic() >= deadline
            ):  # A late readiness return cannot restore success.
                return 5  # Keep startup timing fail-closed.
            try:  # Even after readiness, a nonblocking read can legitimately race.
                packet = endpoint.receive()  # Reuse truncation and ancillary rejection.
            except (
                WouldBlock
            ):  # Temporary unavailability does not create a fresh budget.
                # The next iteration waits for readiness rather than busy-spinning.
                continue
            if mode == "health_close":  # Deliberate peer loss during the exchange.
                return 4  # Normal resource cleanup still runs before child exit.
            if not health_answered:  # Exit is allowed only after one valid challenge.
                response = respond(
                    packet, lifetime, time.monotonic_ns()
                )  # Fixed protocol.
                health_answered = (
                    True  # Never turn a later packet into another challenge.
                )
                if mode == "health_silent":  # Keep the process alive for timeout proof.
                    continue  # The original lifetime still expires without a reply.
                if mode == "health_malformed":  # Deliberately invalid packet fixture.
                    response = b"{}"  # The parent must reject rather than repair it.
                if mode == "health_mismatch":  # Keep valid syntax but change binding.
                    changed = json.loads(
                        response
                    )  # Bounded locally generated fixture data.
                    changed["token"] = (
                        "0" * 64 if changed["token"] != "0" * 64 else "1" * 64
                    )
                    response = json.dumps(changed).encode(
                        "ascii"
                    )  # Still within packet bound.
                if mode in {
                    "health_lifetime",
                    "health_generation",
                }:  # Fixed faults only.
                    changed = json.loads(response)  # Modify bounded local fixture data.
                    if (
                        mode == "health_lifetime"
                    ):  # Keep syntax valid but break identity.
                        changed["watchdog_start_ticks"] += (
                            1  # Not the trusted generation.
                        )
                    else:  # A future time cannot be accepted as a recent generation.
                        changed["generated_ns"] = (
                            lifetime.deadline_ns + 1
                        )  # Beyond expiry.
                    response = json.dumps(changed).encode(
                        "ascii"
                    )  # Whole bounded packet.
                endpoint.send(
                    response
                )  # One all-or-error send, without an automatic retry.
                if (
                    mode == "health_duplicate"
                ):  # Exercise rejection of residual replies.
                    endpoint.send(
                        response
                    )  # Deliberate negative fixture, never a retry.
                # Await only the fixed exit instruction under the same deadline.
                continue
            return (
                0 if packet == b"exit" else 4
            )  # No arbitrary command or second exchange.
        return 5  # A timeout can never be reported as completed fixture work.
    except (
        TransportError
    ) as error:  # Preserve uncertain duplicate cleanup as a distinct failure.
        if not error.cleanup_complete:  # A passing release acknowledgement is required.
            raise FixtureReleaseError("DUPLICATE_RELEASE_UNCONFIRMED") from None
        return 4  # A rejected endpoint cannot produce successful fixture evidence.
    except (
        HealthError,  # Reject bad challenge syntax, lifetime, or generation timing.
        WouldBlock,
        OSError,
    ):  # Other unavailable setup or single-send failures stop the fixture.
        # The parent independently observes unsuccessful exit or missing evidence.
        return 4
    # Attempt both kinds of owned-resource cleanup, even after an earlier error.
    finally:
        failed = (
            False  # Any unconfirmed release overrides an otherwise successful exit.
        )
        if (
            endpoint is not None
        ):  # Only an actually returned duplicate belongs to this object.
            try:  # Closing the copy does not close any retained parent copy.
                endpoint.close()  # Reuse the transport's one-attempt ownership cleanup.
            except (
                TransportError
            ):  # An uncertain close cannot produce a pristine fixture PASS.
                failed = True  # Do not retry the uncertain descriptor number.
        if source_open:  # Adoption failed before the original's close attempt.
            source_open = (
                False  # Record the attempt before the potentially ambiguous call.
            )
            try:  # Only the child's inherited original is owned by this cleanup path.
                os.close(
                    source_fd
                )  # Never close a reported or guessed unrelated descriptor.
            except OSError:  # Preserve uncertainty instead of trying that number again.
                failed = True  # No success is possible after unconfirmed release.
        if failed:  # Cleanup must win over a previously prepared successful return.
            raise FixtureReleaseError(
                "FIXTURE_RELEASE_UNCONFIRMED"
            )  # Never mask it with success.


if __name__ == "__main__":  # Importing the fixture never starts a child or an exchange.
    try:  # Malformed inputs must fail without a private traceback.
        raise SystemExit(main())  # Return only the fixed fixture status.
    except (
        FixtureReleaseError
    ):  # Keep release uncertainty distinct from malformed input.
        raise SystemExit(
            6
        ) from None  # The parent maps this status to an explicit failed attempt.
    except (
        OSError,
        ValueError,
        IndexError,
        ImportError,
    ):  # Unavailable trusted setup cannot pass.
        raise SystemExit(
            4
        ) from None  # Do not echo arguments, paths, or kernel contents.
