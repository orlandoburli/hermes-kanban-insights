"""Usage Report dashboard plugin — backend API routes.

Mounted at /api/plugins/usage-report/ by the dashboard plugin system.
Provides aggregated data on token consumption, task duration by type,
and per-profile breakdowns.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query

# Adapter system — direct path since the server loads this as
# a standalone module (not a package), so relative imports fail.
_adapters_dir = os.path.join(os.path.dirname(__file__), "adapters")
_adapter_init = os.path.join(_adapters_dir, "__init__.py")
_adapters_mod = None
if os.path.exists(_adapter_init):
    import importlib.util as _iu
    _spec = _iu.spec_from_file_location("_usage_report_adapters", _adapter_init,
                                         submodule_search_locations=[_adapters_dir])
    if _spec and _spec.loader:
        _adapters_mod = _iu.module_from_spec(_spec)
        sys.modules["_usage_report_adapters"] = _adapters_mod
        # Also register adapters_base so loaded adapter modules can find it
        _base_path = os.path.join(_adapters_dir, "adapters_base.py")
        if os.path.exists(_base_path):
            _base_spec = _iu.spec_from_file_location("_usage_report_adapters_base", _base_path)
            if _base_spec and _base_spec.loader:
                _base_mod = _iu.module_from_spec(_base_spec)
                sys.modules["_usage_report_adapters_base"] = _base_mod
                sys.modules["adapters_base"] = _base_mod
                _base_spec.loader.exec_module(_base_mod)
        _spec.loader.exec_module(_adapters_mod)
import _usage_report_adapters as adapters  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()

KANBAN_DB = os.path.expanduser("~/.hermes/kanban.db")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kanban_db() -> sqlite3.Connection:
    conn = sqlite3.connect(KANBAN_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    return conn


def _parse_task_type(title: str) -> str:
    """Extract task type from kanban task title — handles project conventions.

    Patterns mapped (from 80 unique titles in the last 30 days):
      [TASK, ...]            → Feature
      [BUG, ...]             → Bug
      [QA, ...]              → QA
      [CHORE]                → Chore
      [CRÍTICO] / [ALTO]     → Bug  (severity-only bracket)
      [MÉDIO]                → Bug
      [B1]-[B5]              → Bug  (bug severity codes)
      BUG: / BUG- / Bugfix:  → Bug
      Fix:                   → Bug
      Corrigir ...           → Bug  (Portuguese "fix")
      QA: / QA               → QA
      Testar / Revalidar     → QA  (testing/review)
      Como PO                → Feature
      cotacao-*              → Feature
      Frontend: / Backend:   → Feature
      Dev server / Tab/Enter → Feature
      Icone / Aba            → Feature
      Navegacao              → Feature
    """
    if not title:
        return "Other"
    import re

    _SKIP_TAGS = {"COT", "GERAL", "HOTFIX"}

    _TYPE_MAP = {
        "BUG": "Bug",
        "CHORE": "Chore",
        "QA": "QA",
        "FEAT": "Feature",
        "FEATURE": "Feature",
        "TASK": "Feature",       # project uses [TASK] for features
        "REFACTOR": "Refactor",
        "DOCS": "Docs",
        "TEST": "Test",
        "OPS": "Ops",
        "SPIKE": "Spike",
    }

    # Severity-only brackets → Bug
    _BUG_SEVERITY = {"CRIT", "CRÍTICO", "CRITICO", "ALTO", "MÉDIO", "MEDIO", "BAIXO",
                     "B1", "B2", "B3", "B4", "B5", "P1", "P2", "P3"}

    stripped = title.strip()

    # ── Prefix-based detection ──────────────────────────────────
    # Bug patterns
    if re.match(r"^BUG[\s:-]", stripped, re.IGNORECASE):
        return "Bug"
    if re.match(r"^Bugfix[\s:]", stripped, re.IGNORECASE):
        return "Bug"
    if re.match(r"^Fix[\s:]", stripped, re.IGNORECASE):
        return "Bug"
    if re.match(r"^Corrigir[\s:]", stripped, re.IGNORECASE):
        return "Bug"

    # QA patterns
    if re.match(r"^QA[\s:]", stripped, re.IGNORECASE):
        return "QA"
    if re.match(r"^Testar[\s:]", stripped, re.IGNORECASE):
        return "QA"
    if re.match(r"^Revalidar[\s:]", stripped, re.IGNORECASE):
        return "QA"

    # Feature patterns
    if re.match(r"^Como\s+PO", stripped, re.IGNORECASE):
        return "Feature"
    if re.match(r"^cotacao[\s-]", stripped, re.IGNORECASE):
        return "Feature"
    if re.match(r"^(Frontend|Backend|Dev)\s*[: ]", stripped, re.IGNORECASE):
        return "Feature"
    if re.match(r"^(Icone|Aba|Tab|Navegacao)", stripped, re.IGNORECASE):
        return "Feature"

    # ── Bracket-based detection ─────────────────────────────────
    matches = re.findall(r"\[(\w+)\]", stripped)

    # First, look for a recognized type tag
    for m in matches:
        upper = m.upper()
        if upper in _SKIP_TAGS:
            continue
        if upper in _TYPE_MAP:
            return _TYPE_MAP[upper]

    # If only severity/priority tags found, it's a Bug
    bracket_tags = {m.upper() for m in matches}
    if bracket_tags and bracket_tags.issubset(_BUG_SEVERITY):
        return "Bug"

    # Last resort: check all brackets against the map
    for m in matches:
        upper = m.upper()
        if upper in _TYPE_MAP:
            return _TYPE_MAP[upper]

    return "Other"


def _fmt_duration(seconds: Optional[int]) -> str:
    """Format seconds to human-readable duration."""
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h < 10:
        return f"{h}h {m}m"
    return f"{h}h {m}m"


def _resolve_cutoff(days: int = 7, start: Optional[str] = None, end: Optional[str] = None) -> tuple[int, int, int]:
    """Resolve cutoff timestamps from days or custom date range.

    Returns (unix_cutoff, unix_cutoff_ms, period_days).
    """
    if start and end:
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            cutoff_s = int(start_dt.timestamp())
            period = max(1, (end_dt - start_dt).days + 1)
            return (cutoff_s, cutoff_s * 1000, period)
        except ValueError:
            pass
    # Default: days ago
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    return (cutoff, cutoff * 1000, days)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_stats(days: int = 7, start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
    """Main stats endpoint: token consumption + task type breakdown + profile breakdown."""
    cutoff_s, cutoff_ms, period_days = _resolve_cutoff(days, start, end)
    task_data = _get_task_stats(cutoff_s, days)
    profile_data = _get_profile_stats(cutoff_s, days)
    daily_breakdown = _get_daily_breakdown(cutoff_s, cutoff_ms, days, start, end)
    token_breakdown = _get_token_breakdown(cutoff_s, cutoff_ms, days, task_data, profile_data)

    return {
        "period_days": period_days,
        "period_start": start,
        "period_end": end,
        "tokens": token_breakdown,
        "tasks": task_data,
        "profiles": profile_data,
        "daily": daily_breakdown,
    }


@router.get("/tokens")
async def get_tokens(days: int = 7):
    task_data = _get_task_stats(days)
    profile_data = _get_profile_stats(days)
    return _get_token_breakdown(days, task_data, profile_data)


@router.get("/tasks")
async def get_tasks(days: int = 7):
    return _get_task_stats(days)


@router.get("/profiles")
async def get_profiles(days: int = 7):
    return _get_profile_stats(days)


@router.get("/daily")
async def get_daily(days: int = 7):
    return _get_daily_breakdown(days)


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------

def _get_token_breakdown(cutoff_s: int, cutoff_ms: int, days: int, task_data: dict, profile_data: dict) -> dict:
    """Token & cost breakdown by profile and task type.

    Uses actual OpenCode total cost, distributed proportionally by
    time spent per profile and per task type.
    """
    try:
        # Cost data from the first available adapter
        adapter = adapters.pick_adapter()
        cost = adapter.fetch(cutoff_ms, 9999999999999) if adapter else adapters.CostData()

        total_cost = cost.total_cost
        total_input = cost.total_input_tokens
        total_output = cost.total_output_tokens
        total_cache = cost.total_cache_read_tokens
        total_sessions = cost.total_sessions

        # Get total duration per profile from profile_data
        profile_durations: dict[str, int] = {}
        total_duration = 0
        for p in profile_data.get("profiles", []):
            dur = p.get("total_duration_seconds", 0)
            profile_durations[p["profile"]] = dur
            total_duration += dur

        # Distribute cost + tokens by profile (proportional to time)
        profiles_list = []
        for p in profile_data.get("profiles", []):
            prof = p["profile"]
            dur = profile_durations.get(prof, 0)
            fraction = dur / total_duration if total_duration > 0 else 0

            prof_cost = round(total_cost * fraction, 2)
            prof_input = int(total_input * fraction)
            prof_output = int(total_output * fraction)
            prof_cache = int(total_cache * fraction)

            # Distribute by task type (proportional to time within profile)
            by_type_list = []
            type_total_dur = sum(bt["duration_seconds"] for bt in p.get("by_type", {}).values())
            for ttype, bt in p.get("by_type", {}).items():
                type_fraction = bt["duration_seconds"] / type_total_dur if type_total_dur > 0 else 0
                by_type_list.append({
                    "type": ttype,
                    "cost": round(prof_cost * type_fraction, 2),
                    "input_tokens": int(prof_input * type_fraction),
                    "output_tokens": int(prof_output * type_fraction),
                    "cache_read_tokens": int(prof_cache * type_fraction),
                    "runs": bt.get("count", 0),
                })

            profiles_list.append({
                "profile": prof,
                "cost": prof_cost,
                "input_tokens": prof_input,
                "output_tokens": prof_output,
                "cache_read_tokens": prof_cache,
                "total_tokens": prof_input + prof_output + prof_cache,
                "runs": p.get("total_runs", 0),
                "by_type": sorted(by_type_list, key=lambda x: x["cost"], reverse=True),
            })

        # Aggregate by task type across all profiles
        type_agg: dict[str, dict] = {}
        for p in profiles_list:
            for bt in p["by_type"]:
                ttype = bt["type"]
                if ttype not in type_agg:
                    type_agg[ttype] = {"cost": 0, "input_tokens": 0, "output_tokens": 0,
                                        "cache_read_tokens": 0, "runs": 0}
                ta = type_agg[ttype]
                ta["cost"] += bt["cost"]
                ta["input_tokens"] += bt["input_tokens"]
                ta["output_tokens"] += bt["output_tokens"]
                ta["cache_read_tokens"] += bt["cache_read_tokens"]
                ta["runs"] += bt["runs"]

        types_list = sorted(
            [{"type": k, **v} for k, v in type_agg.items()],
            key=lambda x: x["cost"], reverse=True
        )

        return {
            "total_cost": round(total_cost, 2),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cache_read_tokens": total_cache,
            "total_sessions": total_sessions,
            "provider": cost.provider,
            "by_profile": profiles_list,
            "by_type": types_list,
            "days": days,
        }
    except Exception as e:
        log.warning("Failed to build token breakdown: %s", e)
        return {"error": str(e), "total_cost": 0, "total_input_tokens": 0,
                "total_output_tokens": 0, "total_cache_read_tokens": 0,
                "total_sessions": 0, "provider": "none", "by_profile": [], "by_type": [], "days": days}


def _get_task_stats(cutoff_s: int, days: int) -> dict:
    """Task type duration breakdown from kanban DB."""
    try:
        conn = _kanban_db()

        rows = conn.execute("""
            SELECT
                t.title,
                tr.started_at,
                tr.ended_at,
                tr.status,
                tr.outcome,
                tr.profile
            FROM task_runs tr
            JOIN tasks t ON t.id = tr.task_id
            WHERE tr.started_at >= ?
              AND tr.ended_at IS NOT NULL
              AND tr.ended_at > tr.started_at
              AND tr.outcome != 'reclaimed'
            ORDER BY tr.started_at DESC
        """, (cutoff_s,)).fetchall()

        # Group by task type
        type_stats: dict[str, dict] = {}
        for r in rows:
            ttype = _parse_task_type(r["title"])
            if ttype not in type_stats:
                type_stats[ttype] = {
                    "type": ttype,
                    "count": 0,
                    "total_duration_seconds": 0,
                    "completed": 0,
                    "blocked": 0,
                    "failed": 0,
                    "runs": [],
                }
            ts = type_stats[ttype]
            ts["count"] += 1
            duration = r["ended_at"] - r["started_at"]
            ts["total_duration_seconds"] += duration
            if r["outcome"] == "completed":
                ts["completed"] += 1
            elif r["outcome"] == "blocked":
                ts["blocked"] += 1
            else:
                ts["failed"] += 1

        types_list = sorted(type_stats.values(), key=lambda x: x["total_duration_seconds"], reverse=True)

        # Format durations
        for ts in types_list:
            avg_s = ts["total_duration_seconds"] // ts["count"] if ts["count"] else 0
            ts["avg_duration"] = _fmt_duration(avg_s)
            ts["total_duration"] = _fmt_duration(ts["total_duration_seconds"])
            ts["total_duration_seconds"] = ts["total_duration_seconds"]

        conn.close()
        return {
            "types": types_list,
            "total_runs": sum(t["count"] for t in types_list),
            "days": days,
        }
    except Exception as e:
        log.warning("Failed to query kanban DB: %s", e)
        return {"error": str(e), "types": [], "total_runs": 0, "days": days}


def _get_profile_stats(cutoff_s: int, days: int) -> dict:
    """Per-profile breakdown from kanban DB."""
    try:
        conn = _kanban_db()

        rows = conn.execute("""
            SELECT
                COALESCE(NULLIF(tr.profile, ''), 'unknown') AS profile,
                tr.status,
                tr.outcome,
                tr.started_at,
                tr.ended_at,
                t.title
            FROM task_runs tr
            JOIN tasks t ON t.id = tr.task_id
            WHERE tr.started_at >= ?
              AND tr.ended_at IS NOT NULL
              AND tr.ended_at > tr.started_at
              AND tr.outcome != 'reclaimed'
            ORDER BY tr.started_at DESC
        """, (cutoff_s,)).fetchall()

        profile_stats: dict[str, dict] = {}
        for r in rows:
            prof = r["profile"]
            if prof not in profile_stats:
                profile_stats[prof] = {
                    "profile": prof,
                    "total_runs": 0,
                    "completed": 0,
                    "blocked": 0,
                    "failed": 0,
                    "total_duration_seconds": 0,
                    "by_type": {},
                }
            ps = profile_stats[prof]
            ps["total_runs"] += 1
            duration = r["ended_at"] - r["started_at"]
            ps["total_duration_seconds"] += duration

            if r["outcome"] == "completed":
                ps["completed"] += 1
            elif r["outcome"] == "blocked":
                ps["blocked"] += 1
            else:
                ps["failed"] += 1

            ttype = _parse_task_type(r["title"])
            if ttype not in ps["by_type"]:
                ps["by_type"][ttype] = {"count": 0, "duration_seconds": 0}
            ps["by_type"][ttype]["count"] += 1
            ps["by_type"][ttype]["duration_seconds"] += duration

        profiles_list = sorted(profile_stats.values(), key=lambda x: x["total_duration_seconds"], reverse=True)

        # Format durations
        for ps in profiles_list:
            ps["total_duration"] = _fmt_duration(ps["total_duration_seconds"])
            avg_s = ps["total_duration_seconds"] // ps["total_runs"] if ps["total_runs"] else 0
            ps["avg_duration"] = _fmt_duration(avg_s)
            # Format by_type durations
            for bt in ps["by_type"].values():
                bt["duration"] = _fmt_duration(bt["duration_seconds"])

        conn.close()
        return {
            "profiles": profiles_list,
            "total_runs": sum(p["total_runs"] for p in profiles_list),
            "days": days,
        }
    except Exception as e:
        log.warning("Failed to query kanban DB: %s", e)
        return {"error": str(e), "profiles": [], "total_runs": 0, "days": days}


def _get_daily_breakdown(cutoff_s: int, cutoff_ms: int, days: int, start: Optional[str] = None, end: Optional[str] = None) -> dict:
    """Per-day breakdown combining token costs and task activity.

    Uses session-level aggregate cost from OpenCode (same source as
    Token & Cost sections), distributed proportionally by task time
    per day — consistent even when per-message data hasn't synced.
    """
    try:
        # Aggregate cost from the first available adapter (same source
        # as _get_token_breakdown — keeps daily chart consistent)
        adapter = adapters.pick_adapter()
        cost = adapter.fetch(cutoff_ms, 9999999999999) if adapter else adapters.CostData()

        total_cost = cost.total_cost
        total_input = cost.total_input_tokens

        # Task duration per day from kanban
        conn2 = _kanban_db()
        task_rows = conn2.execute("""
            SELECT
                date(tr.started_at, 'unixepoch') AS day,
                SUM(tr.ended_at - tr.started_at) AS duration_seconds,
                COUNT(*) AS runs,
                SUM(CASE WHEN tr.outcome = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN tr.outcome = 'blocked' THEN 1 ELSE 0 END) AS blocked,
                COALESCE(NULLIF(tr.profile, ''), 'unknown') AS profile
            FROM task_runs tr
            WHERE tr.started_at >= ?
              AND tr.ended_at IS NOT NULL
              AND tr.ended_at > tr.started_at
              AND tr.outcome != 'reclaimed'
            GROUP BY day, tr.profile
            ORDER BY day
        """, (cutoff_s,)).fetchall()
        conn2.close()

        # Aggregate per day
        day_durations: dict[str, int] = {}
        day_profiles: dict[str, dict] = {}
        day_runs: dict[str, dict] = {}
        for r in task_rows:
            d = r["day"]
            secs = r["duration_seconds"] or 0
            day_durations[d] = day_durations.get(d, 0) + secs
            if d not in day_runs:
                day_runs[d] = {"runs": 0, "completed": 0, "blocked": 0}
            day_runs[d]["runs"] += r["runs"]
            day_runs[d]["completed"] += r["completed"]
            day_runs[d]["blocked"] += r["blocked"]
            if d not in day_profiles:
                day_profiles[d] = {}
            day_profiles[d][r["profile"]] = day_profiles[d].get(r["profile"], 0) + r["runs"]

        total_task_duration = sum(day_durations.values())

        # Determine date range
        today = datetime.now(timezone.utc).date()
        if start and end:
            try:
                start_d = datetime.strptime(start, "%Y-%m-%d").date()
                end_d = datetime.strptime(end, "%Y-%m-%d").date()
                period_days = (end_d - start_d).days
                fill_start, fill_end = start_d, end_d
            except ValueError:
                fill_start = today - timedelta(days=days - 1)
                fill_end = today
        else:
            fill_start = today - timedelta(days=days - 1)
            fill_end = today

        total_period = (fill_end - fill_start).days + 1
        days_list = []
        for i in range(total_period):
            d = (fill_start + timedelta(days=i)).isoformat()
            day_dur = day_durations.get(d, 0)
            fraction = day_dur / total_task_duration if total_task_duration > 0 else 1.0 / total_period
            dr = day_runs.get(d, {"runs": 0, "completed": 0, "blocked": 0})
            days_list.append({
                "day": d,
                "cost": round(total_cost * fraction, 4),
                "input_tokens": int(total_input * fraction),
                "task_runs": dr["runs"],
                "task_completed": dr["completed"],
                "task_blocked": dr["blocked"],
                "profiles": day_profiles.get(d, {}),
            })

        return {"days": days_list, "period_days": len(days_list)}
    except Exception as e:
        log.warning("Failed to build daily breakdown: %s", e)
        return {"error": str(e), "days": [], "period_days": days}
