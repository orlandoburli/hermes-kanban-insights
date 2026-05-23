# Cost Adapters

Adapters are how Hermes Kanban Insights fetches cost and token data.  
Each adapter is a Python module in this directory that implements the `CostAdapter` interface.

## Interface

```python
from adapters_base import CostAdapter, CostData

class Adapter(CostAdapter):
    name = "My Adapter"          # human-readable name

    def is_available(self) -> bool:
        """Return True when this data source is reachable."""
        ...

    def fetch(self, start_ms: int, end_ms: int) -> CostData:
        """Return aggregated cost/token data for the time window.

        Args:
            start_ms: Unix timestamp in milliseconds (inclusive)
            end_ms:   Unix timestamp in milliseconds (exclusive)

        Returns:
            CostData with totals
        """
        ...
```

## `CostData`

| Field | Type | Description |
|-------|------|-------------|
| `total_cost` | float | USD total |
| `total_input_tokens` | int | Total input tokens |
| `total_output_tokens` | int | Total output tokens |
| `total_cache_read_tokens` | int | Total cache-read tokens |
| `total_sessions` | int | Number of sessions |
| `provider` | str | Display name for the UI |

## Discovery

The plugin scans this directory alphabetically and uses the **first adapter** whose `is_available()` returns `True`.  
Adapters starting with `_` are ignored.

## Built-in adapters

### `opencode.py`

Reads from the [OpenCode](https://github.com/sst/opencode) local SQLite database at `~/.local/share/opencode/opencode.db`. Available automatically when the database file exists.

## Example: Hermes State adapter

```python
import sqlite3, os
from adapters_base import CostAdapter, CostData

HERMES_DB = os.path.expanduser("~/.hermes/state.db")

class Adapter(CostAdapter):
    name = "Hermes State"

    def is_available(self):
        return os.path.isfile(HERMES_DB)

    def fetch(self, start_ms, end_ms):
        conn = sqlite3.connect(HERMES_DB)
        row = conn.execute("""
            SELECT
                SUM(COALESCE(estimated_cost_usd, 0)) AS cost,
                SUM(COALESCE(input_tokens, 0)) AS inp,
                SUM(COALESCE(output_tokens, 0)) AS out,
                COUNT(*) AS sessions
            FROM sessions
            WHERE started_at >= ? AND started_at < ?
        """, (start_ms / 1000, end_ms / 1000)).fetchone()
        conn.close()
        return CostData(
            total_cost=row[0] or 0,
            total_input_tokens=row[1] or 0,
            total_output_tokens=row[2] or 0,
            total_sessions=row[3] or 0,
            provider=self.name,
        )
```

Just drop this as `hermes_state.py` in this directory and restart the dashboard.
