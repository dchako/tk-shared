"""
Deadline propagation for inter-service HTTP calls.

A deadline is an absolute epoch timestamp (milliseconds) that constrains
how long the *entire* distributed request (across hops) may take.

Usage (producer side — e.g. tk-mcp calling a tool):

    token = set_deadline(epoch_ms=int(time.time() * 1000) + 60_000)
    try:
        await do_something()
    finally:
        reset_deadline(token)

Usage (consumer side — inside a FastAPI service):

    Add DeadlineMiddleware (in thunderclouds_shared.http.middleware) to the
    FastAPI app.  The middleware reads X-Deadline-Epoch-Ms, populates the
    ContextVar, and returns 504 if already expired.

Client propagation:

    InternalServiceClient._merge_headers() automatically injects the header
    when a deadline is active, and aborts retries when the remaining budget
    is not enough for another sleep.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar, Token
from typing import Optional

logger = logging.getLogger(__name__)

# ── Public constant ────────────────────────────────────────────────────────────

HEADER_NAME = "X-Deadline-Epoch-Ms"

# ── Internal ContextVar ────────────────────────────────────────────────────────

_deadline_epoch_ms_var: ContextVar[Optional[int]] = ContextVar(
    "_deadline_epoch_ms", default=None
)


# ── Setter / getter helpers ────────────────────────────────────────────────────


def set_deadline(epoch_ms: int) -> "Token[Optional[int]]":
    """Store *epoch_ms* as the current deadline and return the reset token."""
    return _deadline_epoch_ms_var.set(epoch_ms)


def reset_deadline(token: "Token[Optional[int]]") -> None:
    """Restore the ContextVar to the state before the matching set_deadline."""
    _deadline_epoch_ms_var.reset(token)


def set_deadline_from_header(value: str) -> "Token[Optional[int]] | None":
    """Parse *value* (a string) as an integer epoch-ms deadline and set it.

    Returns the reset token on success, or None if *value* cannot be parsed
    (the error is swallowed silently — an invalid header is treated as absent).
    """
    try:
        epoch_ms = int(value)
    except (ValueError, TypeError):
        logger.debug("set_deadline_from_header: invalid value %r, ignoring", value)
        return None
    return set_deadline(epoch_ms)


def remaining_ms() -> Optional[int]:
    """Return how many milliseconds remain until the deadline, or None if no deadline is set.

    May return a negative value if the deadline has already passed.
    """
    deadline = _deadline_epoch_ms_var.get()
    if deadline is None:
        return None
    now_ms = int(time.time() * 1000)
    return deadline - now_ms


def deadline_header_value() -> Optional[str]:
    """Return the header string to propagate, or None if no deadline is active."""
    deadline = _deadline_epoch_ms_var.get()
    if deadline is None:
        return None
    return str(deadline)


# ── Error type ─────────────────────────────────────────────────────────────────


class DeadlineExceededError(RuntimeError):
    """Raised by check_deadline() when the deadline has already passed."""

    def __init__(self, remaining_ms: int) -> None:
        self.remaining_ms = remaining_ms
        super().__init__(
            f"Deadline exceeded (remaining_ms={remaining_ms})"
        )


# ── Guard helper ───────────────────────────────────────────────────────────────


def check_deadline(min_budget_ms: int = 500) -> None:
    """Raise DeadlineExceededError if the deadline has passed or there is less
    than *min_budget_ms* milliseconds left.

    Does nothing if no deadline is active.
    """
    rem = remaining_ms()
    if rem is None:
        return  # no deadline — always allow
    if rem < min_budget_ms:
        raise DeadlineExceededError(remaining_ms=rem)
