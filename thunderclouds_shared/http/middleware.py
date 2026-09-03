"""
Starlette/FastAPI middleware that reads the X-Deadline-Epoch-Ms header and
either short-circuits the request with 504 (if already expired) or sets
the ContextVar for the duration of the request.
"""

from __future__ import annotations

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from thunderclouds_shared.http.deadline import (
    HEADER_NAME,
    remaining_ms,
    reset_deadline,
    set_deadline_from_header,
)

logger = logging.getLogger(__name__)


class DeadlineMiddleware(BaseHTTPMiddleware):
    """
    Reads *X-Deadline-Epoch-Ms* from incoming requests.

    Behaviour:
    - Header absent   → request passes through unchanged.
    - Header present, deadline not yet expired → deadline ContextVar is set for
      the duration of the request and reset in the finally block.
    - Header present, deadline already expired → returns HTTP 504 immediately
      without dispatching the request to the route handler.

    The body of a 504 response is a JSON object:
        {"error": "deadline_exceeded", "remaining_ms": <negative int>}
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        raw = request.headers.get(HEADER_NAME)
        if raw is None:
            # No deadline header — passthrough
            return await call_next(request)

        token = set_deadline_from_header(raw)
        if token is None:
            # Unparseable value — treat as absent (silent ignore)
            return await call_next(request)

        rem = remaining_ms()
        if rem is not None and rem <= 0:
            # Already expired before we even start processing — enforce the deadline
            # by returning 504 immediately without dispatching to the route handler.
            logger.warning(
                "deadline_enforced path=%s remaining_ms=%d",
                request.url.path,
                rem,
                extra={
                    "deadline_enforced": True,
                    "remaining_ms": rem,
                    "path": request.url.path,
                },
            )
            reset_deadline(token)
            body = json.dumps({"error": "deadline_exceeded", "remaining_ms": rem})
            return Response(
                content=body,
                status_code=504,
                media_type="application/json",
            )

        try:
            response = await call_next(request)
        finally:
            reset_deadline(token)

        return response
