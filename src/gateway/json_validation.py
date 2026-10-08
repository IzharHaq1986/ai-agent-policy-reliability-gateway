"""Shared JSON validation hooks for untrusted input."""

from typing import NoReturn


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> NoReturn:
    raise ValueError("Non-finite JSON number")
