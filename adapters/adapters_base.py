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


class CostAdapter(ABC):
    """Base class for cost data source adapters."""

    @abstractmethod
    def fetch(self, start_ms: int, end_ms: int) -> CostData:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
