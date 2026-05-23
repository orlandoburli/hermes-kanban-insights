# Hermes Kanban Insights

Dashboard plugin for [Hermes Agent](https://hermes-agent.nousresearch.com) that shows **token consumption, task duration by type, and per-profile breakdown** from your kanban board.

![](screenshot.png)

## Features

- **Daily Activity & Cost** — line chart with dual Y-axis (cost + tokens) using Chart.js
- **Time per Task Type** — Bug, Feature, QA, Chore with runs, completions, blocked count
- **Time per Profile** — engineer, qa, po with task-type breakdown badges
- **Token & Cost by Task Type** — distributed proportionally from your cost provider
- **Token & Cost by Profile** — per-profile with sub-breakdown
- **Custom date ranges** — presets (7d, 14d, 30d) or arbitrary start/end via URL params
- **URL-persisted filters** — refresh-safe: `/kanban-insights?days=7` or `?start=2026-05-01&end=2026-05-15`

## Installation

```bash
# Clone into Hermes user plugins
git clone https://github.com/orlandoburli/hermes-kanban-insights.git \
  ~/.hermes/plugins/hermes-kanban-insights
```

Then restart the dashboard:

```bash
hermes dashboard --stop
hermes dashboard --port 9119 --no-open
```

Navigate to `/kanban-insights` in the Hermes dashboard.

## Cost Adapters

The plugin uses a pluggable adapter system to fetch cost and token data.  
Built-in adapters:

| Adapter | Source | When to use |
|---------|--------|-------------|
| `opencode.py` | `~/.local/share/opencode/opencode.db` | Default — works with OpenCode Go provider |

Add new adapters by dropping a Python file into `adapters/`.  
See [adapters/README.md](adapters/README.md).

## Data sources

| Data | Source | Description |
|------|--------|-------------|
| Task runs, durations, profiles | `~/.hermes/kanban.db` | Kanban board SQLite |
| Cost, tokens, sessions | Cost adapter | Pluggable (default: OpenCode) |

## Requirements

- Hermes Agent 0.14.0+
- Kanban board with task_runs (profiles: engineer, qa, po)

## License

MIT
