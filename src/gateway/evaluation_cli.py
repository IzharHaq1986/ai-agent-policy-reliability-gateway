"""Bounded command-line access to deterministic policy evaluation."""

import json
import sys

from gateway.cli_output import write_stdout
from gateway.evaluation import EvaluationDataError, evaluate_dataset
from gateway.evaluation_input import MAX_DATASET_BYTES, load_evaluation_dataset

_MAX_DATASET_BYTES = MAX_DATASET_BYTES


def _fail(code: str) -> int:
    sys.stderr.write(code + "\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    """Evaluate one explicit dataset path without exposing failure details."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        return _fail("USAGE_ERROR")

    try:
        _, dataset = load_evaluation_dataset(arguments[0])
    except (OSError, ValueError, RecursionError):
        return _fail("INPUT_ERROR")

    try:
        report = evaluate_dataset(dataset)
        output = (
            json.dumps(
                report,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        status = 0 if report["failed"] == 0 else 1
    except EvaluationDataError:
        return _fail("INPUT_ERROR")
    except Exception:
        return _fail("EVALUATION_ERROR")

    try:
        write_stdout(output)
    except OSError:
        return _fail("OUTPUT_ERROR")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
