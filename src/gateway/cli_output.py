"""Complete stdout delivery with best-effort failed-stream recovery."""

import os
import sys


def write_stdout(output: str) -> None:
    """Require a full write and flush; partial output may remain on failure."""
    try:
        written = sys.stdout.write(output)
        if type(written) is not int or written != len(output):
            raise OSError("Incomplete stdout write")
        sys.stdout.flush()
    except OSError:
        # Prevent interpreter shutdown from retrying the failed output stream.
        try:
            with open(os.devnull, "wb") as discard:
                os.dup2(discard.fileno(), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        raise
