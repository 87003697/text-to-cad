#!/usr/bin/env python3
"""THROWAWAY bounded admission seam for SAR-007 decision evidence."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable, TypeVar


FIRST_RELEASE_ACTIVE_CAP = 4
_T = TypeVar("_T")


@dataclass(frozen=True)
class AdmissionSnapshot:
    active: tuple[str, ...]
    queued: tuple[str, ...]
    observed_peak: int


class AdmissionController:
    """FIFO admission with one hard active cap and observable queue state."""

    def __init__(self, active_cap: int = FIRST_RELEASE_ACTIVE_CAP) -> None:
        if active_cap != FIRST_RELEASE_ACTIVE_CAP:
            raise ValueError("first-release active cap is exactly four")
        self._active_cap = active_cap
        self._condition = threading.Condition()
        self._active: list[str] = []
        self._queued: list[str] = []
        self._observed_peak = 0

    def snapshot(self) -> AdmissionSnapshot:
        with self._condition:
            return AdmissionSnapshot(
                tuple(self._active), tuple(self._queued), self._observed_peak,
            )

    def wait_until(self, predicate: Callable[[AdmissionSnapshot], bool]) -> None:
        with self._condition:
            if not self._condition.wait_for(
                lambda: predicate(self.snapshot()), timeout=10,
            ):
                raise TimeoutError("admission observation timed out")

    def run(self, execution_id: str, operation: Callable[[], _T]) -> _T:
        with self._condition:
            if execution_id in self._active or execution_id in self._queued:
                raise ValueError("execution identity is already admitted or queued")
            self._queued.append(execution_id)
            self._condition.notify_all()
            self._condition.wait_for(
                lambda: self._queued[0] == execution_id
                and len(self._active) < self._active_cap,
            )
            assert self._queued.pop(0) == execution_id
            self._active.append(execution_id)
            self._observed_peak = max(self._observed_peak, len(self._active))
            self._condition.notify_all()
        try:
            return operation()
        finally:
            with self._condition:
                self._active.remove(execution_id)
                self._condition.notify_all()
