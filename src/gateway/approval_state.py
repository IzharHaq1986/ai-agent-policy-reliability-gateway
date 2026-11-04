"""Rules for proposed synthetic approval state transitions."""


class ApprovalStateValidationError(ValueError):
    """A supplied state is not a recognized plain string."""


# List each known state and its permitted destinations.
# Empty tuples mean that the state has no outgoing transitions.
_NEXT_STATES: dict[str, tuple[str, ...]] = {
    "pending": ("approved", "rejected", "expired", "revoked"),
    "approved": ("expired", "revoked", "consumed"),
    "rejected": (),
    "expired": (),
    "revoked": (),
    "consumed": (),
}


def _validate_state(value: object) -> str:
    """Reject unknown labels and values that require conversion."""
    if type(value) is not str or value not in _NEXT_STATES:
        raise ApprovalStateValidationError("Invalid approval state")
    return value


def is_approval_transition_allowed(current_state: object, next_state: object) -> bool:
    """Check the state pair without changing a record or granting authority."""
    current = _validate_state(current_state)
    proposed = _validate_state(next_state)
    return proposed in _NEXT_STATES[current]
