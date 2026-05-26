"""
Base classes for cost adapters.

Shared between ``__init__.py`` and individual adapter modules.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class CostData:
    """Normalised cost & token data returned by every adapter."""
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_sessions: int = 0
    provider: str = "unknown"


@dataclass
class DayCostData:
    """Per-day cost & token data for daily breakdown charts."""
    day: str  # YYYY-MM-DD
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    sessions: int = 0


class CostAdapter(ABC):
    """Base class for cost data source adapters."""

    @abstractmethod
    def fetch(self, start_ms: int, end_ms: int) -> CostData:
        ...

    @abstractmethod
    def fetch_daily(self, start_ms: int, end_ms: int) -> list[DayCostData]:
        """
        Return per-day cost data grouped by UTC date.

        Default fallback: return a single 'all' entry with the aggregate.
        Adapters that support per-day queries should override this with
        a GROUP BY date query.
        """
        c = self.fetch(start_ms, end_ms)
        return [DayCostData(day="all", cost=c.total_cost,
                            input_tokens=c.total_input_tokens,
                            output_tokens=c.total_output_tokens,
                            cache_read_tokens=c.total_cache_read_tokens,
                            sessions=c.total_sessions)]

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
