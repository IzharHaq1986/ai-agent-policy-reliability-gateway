"""Bounded loading of evaluation datasets and their exact source bytes."""

import json
from pathlib import Path

from gateway.json_validation import reject_constant, unique_object

MAX_DATASET_BYTES = 1024 * 1024


def load_evaluation_dataset(path: str | Path) -> tuple[bytes, object]:
    """Read once and parse the captured bytes without modifying the file."""
    with Path(path).open("rb") as stream:
        body = stream.read(MAX_DATASET_BYTES + 1)
    if len(body) > MAX_DATASET_BYTES:
        raise ValueError("Evaluation dataset exceeds byte limit")
    dataset: object = json.loads(
        body.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    return body, dataset
