"""Shared retry helper with exponential backoff."""

from __future__ import annotations

import functools
import logging
import random
import time
from typing import Callable, Iterable, Type

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 2.0


def retry(
    func: Callable,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    exceptions: Iterable[Type[BaseException]] = (Exception,),
    base_delay: float = DEFAULT_BASE_DELAY,
    label: str = "",
):
    """Call func, retrying with exponential backoff + jitter.

    Usage:
        data = retry(call_api, exceptions=(SomeError,), label="llm")
    """
    exc_types: tuple = tuple(exceptions)
    name = label or getattr(func, "__name__", "operation")

    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except exc_types as exc:  # noqa: PERF203 - retry loop
            last_error = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, 0.5 * base_delay)
            logger.warning(
                "%s failed (attempt %d/%d): %s -- retrying in %.1fs",
                name,
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)

    logger.error("%s failed after %d attempts: %s", name, attempts, last_error)
    raise last_error  # type: ignore[misc]


def retryable(func: Callable, **kwargs):
    """Decorator form of :func:`retry`."""

    @functools.wraps(func)
    def wrapper(*args, **kw):
        bound = functools.partial(func, *args, **kw)
        return retry(bound, **kwargs)

    return wrapper