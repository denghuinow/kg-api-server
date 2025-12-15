from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    initial_backoff_s: float
    max_backoff_s: float
    backoff_multiplier: float


def _is_retryable_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "rate limit" in msg or "429" in msg:
        return True
    if "timeout" in msg or "timed out" in msg:
        return True
    if "temporarily unavailable" in msg:
        return True
    if "connection reset" in msg or "connection aborted" in msg:
        return True
    if "5xx" in msg or "502" in msg or "503" in msg or "504" in msg:
        return True
    return False


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    is_retryable: Optional[Callable[[BaseException], bool]] = None,
) -> T:
    is_retryable = is_retryable or _is_retryable_error
    attempt = 0
    backoff = float(max(0.0, policy.initial_backoff_s))

    while True:
        try:
            return await fn()
        except BaseException as e:
            if attempt >= int(max(0, policy.max_retries)) or not is_retryable(e):
                raise
            await asyncio.sleep(min(backoff, float(max(0.0, policy.max_backoff_s))))
            attempt += 1
            backoff = min(
                float(max(0.0, policy.max_backoff_s)),
                backoff * float(max(1.0, policy.backoff_multiplier)),
            )

