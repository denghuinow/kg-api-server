from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    capacity: float
    refill_per_s: float
    available: float
    last_ts: float

    def refill(self, now: float) -> None:
        if self.refill_per_s <= 0:
            self.available = self.capacity
            self.last_ts = now
            return
        dt = max(0.0, now - self.last_ts)
        self.available = min(self.capacity, self.available + dt * self.refill_per_s)
        self.last_ts = now


class AsyncRateLimiter:
    def __init__(self, rpm: int, tpm: int):
        self._lock = asyncio.Lock()
        now = time.monotonic()

        req_capacity = float(rpm) if rpm > 0 else 0.0
        tok_capacity = float(tpm) if tpm > 0 else 0.0

        self._req = _Bucket(
            capacity=req_capacity,
            refill_per_s=(req_capacity / 60.0) if req_capacity > 0 else 0.0,
            available=req_capacity,
            last_ts=now,
        )
        self._tok = _Bucket(
            capacity=tok_capacity,
            refill_per_s=(tok_capacity / 60.0) if tok_capacity > 0 else 0.0,
            available=tok_capacity,
            last_ts=now,
        )

    async def acquire(self, requests: int, tokens: int) -> None:
        if self._req.capacity <= 0 and self._tok.capacity <= 0:
            return

        req_need = float(max(0, requests))
        tok_need = float(max(0, tokens))

        while True:
            async with self._lock:
                now = time.monotonic()
                self._req.refill(now)
                self._tok.refill(now)

                req_ok = self._req.capacity <= 0 or self._req.available >= req_need
                tok_ok = self._tok.capacity <= 0 or self._tok.available >= tok_need
                if req_ok and tok_ok:
                    if self._req.capacity > 0:
                        self._req.available -= req_need
                    if self._tok.capacity > 0:
                        self._tok.available -= tok_need
                    return

                wait_req = 0.0
                if self._req.capacity > 0 and not req_ok and self._req.refill_per_s > 0:
                    wait_req = (req_need - self._req.available) / self._req.refill_per_s

                wait_tok = 0.0
                if self._tok.capacity > 0 and not tok_ok and self._tok.refill_per_s > 0:
                    wait_tok = (tok_need - self._tok.available) / self._tok.refill_per_s

                wait_s = max(wait_req, wait_tok, 0.05)
            await asyncio.sleep(min(wait_s, 5.0))

