"""
OpenCode SQLite adapter.

Reads cost and token data from the OpenCode provider's local database
at ``~/.local/share/opencode/opencode.db``.
"""

from __future__ import annotations

import logging
import os
import sqlite3

from adapters_base import CostAdapter, CostData

log = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")


class Adapter(CostAdapter):
    name = "OpenCode SQLite"

    def is_available(self) -> bool:
        return os.path.isfile(DB_PATH)

    def fetch(self, start_ms: int, end_ms: int) -> CostData:
        if not self.is_available():
            return CostData(provider=self.name)

        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = 1")

            row = conn.execute("""
                SELECT
                    ROUND(SUM(COALESCE(cost, 0)), 4) AS total_cost,
                    SUM(COALESCE(tokens_input, 0)) AS total_input,
                    SUM(COALESCE(tokens_output, 0)) AS total_output,
                    SUM(COALESCE(tokens_cache_read, 0)) AS total_cache,
                    COUNT(*) AS sessions
                FROM session
                WHERE time_created >= ?
                  AND time_created < ?
                  AND cost > 0
            """, (start_ms, end_ms)).fetchone()

            conn.close()

            return CostData(
                total_cost=float(row["total_cost"]) if row and row["total_cost"] else 0,
                total_input_tokens=int(row["total_input"]) if row and row["total_input"] else 0,
                total_output_tokens=int(row["total_output"]) if row and row["total_output"] else 0,
                total_cache_read_tokens=int(row["total_cache"]) if row and row["total_cache"] else 0,
                total_sessions=int(row["sessions"]) if row and row["sessions"] else 0,
                provider=self.name,
            )
        except Exception as e:
            log.warning("OpenCode adapter error: %s", e)
            return CostData(provider=self.name)
