"""Command-line delivery of traceable model-free evaluation artifacts."""

import json
import sys

from gateway.cli_output import write_stdout
from gateway.evaluation_artifact import (
    ArtifactGenerationError,
    generate_evaluation_artifact,
)

_GENERATION_ERRORS = frozenset(
    {"INPUT_ERROR", "CODE_IDENTITY_ERROR", "EVALUATION_ERROR"}
)


def _fail(code: str) -> int:
    try:
        sys.stderr.write(code + "\n")
    except (OSError, ValueError):
        pass
    return 2


def main(argv: list[str] | None = None) -> int:
    """Generate one artifact without creating files or exposing error details."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        return _fail("USAGE_ERROR")

    try:
        artifact = generate_evaluation_artifact(arguments[0])
        status = 0 if artifact["failed"] == 0 else 1
    except ArtifactGenerationError as exc:
        code = str(exc)
        return _fail(code if code in _GENERATION_ERRORS else "EVALUATION_ERROR")
    except Exception:
        return _fail("EVALUATION_ERROR")

    try:
        output = (
            json.dumps(
                artifact,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
    except Exception:
        return _fail("SERIALIZATION_ERROR")

    try:
        write_stdout(output)
    except (OSError, ValueError):
        return _fail("OUTPUT_ERROR")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
