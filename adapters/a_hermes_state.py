"""
Hermes State DB adapter.

Reads token counts from Hermes profile state.db files (engineer, po, qa)
and the main state.db, then ESTIMATES costs based on DeepSeek V4 Flash
pricing via opencode-go provider.

Why estimation instead of real cost?
  The opencode-go provider does NOT report cost back to Hermes —
  estimated_cost_usd is always 0.0 with cost_status='unknown'.
  We have exact token counts, so we estimate using known pricing.

Pricing (DeepSeek V4 Flash via opencode-go):
  Input:          $0.25 / 1M tokens
  Output:         $1.00 / 1M tokens
  Cache Read:     $0.075 / 1M tokens  (70% discount on input)
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

from adapters_base import CostAdapter, CostData, DayCostData

log = logging.getLogger(__name__)

# DeepSeek V4 Flash pricing (USD per 1M tokens) via opencode-go
PRICE_INPUT = 0.25
PRICE_OUTPUT = 1.00
PRICE_CACHE_READ = 0.075  # ~70% discount on input

# Hermes profiles to scan (in priority/display order)
PROFILES = [
    ("engineer", os.path.expanduser("~/.hermes/profiles/engineer/state.db")),
    ("qa", os.path.expanduser("~/.hermes/profiles/qa/state.db")),
    ("po", os.path.expanduser("~/.hermes/profiles/po/state.db")),
]

# Main session state.db (your interactive Hermes sessions)
MAIN_DB = os.path.expanduser("~/.hermes/state.db")


def _estimate_cost(input_tokens: int, output_tokens: int, cache_read_tokens: int) -> float:
    """Estimate cost from token counts using DeepSeek V4 Flash pricing."""
    return (
        (input_tokens / 1_000_000) * PRICE_INPUT
        + (output_tokens / 1_000_000) * PRICE_OUTPUT
        + (cache_read_tokens / 1_000_000) * PRICE_CACHE_READ
    )


def _list_databases() -> list[tuple[str, str]]:
    """Return list of (label, path) for all known Hermes state databases."""
    dbs = []
    for label, path in PROFILES:
        if os.path.isfile(path):
            dbs.append((label, path))
    if os.path.isfile(MAIN_DB):
        dbs.append(("default", MAIN_DB))
    return dbs


class Adapter(CostAdapter):
    name = "Hermes State DB"

    def is_available(self) -> bool:
        return len(_list_databases()) > 0

    def fetch(self, start_ms: int, end_ms: int) -> CostData:
        if not self.is_available():
            return CostData(provider=self.name)

        total_input = 0
        total_output = 0
        total_cache = 0
        total_sessions = 0
        active_dbs = 0

        for label, db_path in _list_databases():
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = 1")

                # Check if sessions table exists
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
                ).fetchone()
                if not table_check:
                    conn.close()
                    continue

                row = conn.execute("""
                    SELECT
                        COALESCE(SUM(input_tokens), 0) AS total_input,
                        COALESCE(SUM(output_tokens), 0) AS total_output,
                        COALESCE(SUM(cache_read_tokens), 0) AS total_cache,
                        COUNT(*) AS sessions
                    FROM sessions
                    WHERE started_at >= (? / 1000)
                      AND started_at < (? / 1000)
                      AND input_tokens > 0
                """, (start_ms, end_ms)).fetchone()

                conn.close()

                total_input += int(row["total_input"]) if row else 0
                total_output += int(row["total_output"]) if row else 0
                total_cache += int(row["total_cache"]) if row else 0
                total_sessions += int(row["sessions"]) if row else 0
                active_dbs += 1

            except Exception as e:
                log.warning("Hermes state adapter (%s): %s", label, e)

        estimated_cost = _estimate_cost(total_input, total_output, total_cache)

        return CostData(
            total_cost=round(estimated_cost, 4),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cache_read_tokens=total_cache,
            total_sessions=total_sessions,
            provider=f"{self.name} ({active_dbs} databases)",
        )

    def fetch_daily(self, start_ms: int, end_ms: int) -> list[DayCostData]:
        if not self.is_available():
            return []

        # Aggregate per-day across all databases
        day_agg: dict[str, DayCostData] = {}

        for label, db_path in _list_databases():
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = 1")

                # Check if sessions table exists
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
                ).fetchone()
                if not table_check:
                    conn.close()
                    continue

                rows = conn.execute("""
                    SELECT
                        date(started_at, 'unixepoch') AS day,
                        COALESCE(SUM(input_tokens), 0) AS total_input,
                        COALESCE(SUM(output_tokens), 0) AS total_output,
                        COALESCE(SUM(cache_read_tokens), 0) AS total_cache,
                        COUNT(*) AS sessions
                    FROM sessions
                    WHERE started_at >= (? / 1000)
                      AND started_at < (? / 1000)
                      AND input_tokens > 0
                    GROUP BY day
                    ORDER BY day
                """, (start_ms, end_ms)).fetchall()

                conn.close()

                for r in rows:
                    d = r["day"]
                    if d not in day_agg:
                        day_agg[d] = DayCostData(day=d)
                    day_agg[d].input_tokens += int(r["total_input"]) if r["total_input"] else 0
                    day_agg[d].output_tokens += int(r["total_output"]) if r["total_output"] else 0
                    day_agg[d].cache_read_tokens += int(r["total_cache"]) if r["total_cache"] else 0
                    day_agg[d].sessions += int(r["sessions"]) if r["sessions"] else 0

            except Exception as e:
                log.warning("Hermes state adapter fetch_daily (%s): %s", label, e)

        # Compute estimated cost per day
        for d, dc in day_agg.items():
            dc.cost = round(_estimate_cost(dc.input_tokens, dc.output_tokens, dc.cache_read_tokens), 4)

        return sorted(day_agg.values(), key=lambda x: x.day)
