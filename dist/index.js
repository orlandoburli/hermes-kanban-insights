/**
 * Hermes Kanban Insights — Dashboard Plugin
 *
 * Token consumption, task duration by type, and per-profile breakdown
 * with daily, weekly, monthly, and custom date ranges.
 *
 * Uses Chart.js (loaded from CDN) for interactive charts with:
 * - Tooltips on hover
 * - Click legend to toggle series
 * - Responsive auto-resize
 * - Export as PNG
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;

  const {
    Card, CardHeader, CardTitle, CardContent,
    Badge, Button, Tabs, TabsList, TabsTrigger, Separator,
  } = SDK.components;
  const { useState, useEffect, useCallback, useRef } = SDK.hooks;

  // ── Load Chart.js from CDN ─────────────────────────────────────

  let chartJsLoaded = false;
  const chartJsCallbacks = [];

  function onChartJsReady(cb) {
    if (window.Chart) {
      cb(window.Chart);
      return;
    }
    chartJsCallbacks.push(cb);
    if (!chartJsLoaded) {
      chartJsLoaded = true;
      var s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js";
      s.onload = function () {
        // Register the finisher plugin after Chart is loaded
        var callbacks = chartJsCallbacks.slice();
        chartJsCallbacks.length = 0;
        callbacks.forEach(function (cb) { cb(window.Chart); });
      };
      document.head.appendChild(s);
    }
  }

  // ── Helpers ──────────────────────────────────────────────────────

  function fmt(num) {
    if (num === 0) return "0";
    if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
    if (num >= 1000) return (num / 1000).toFixed(0) + "K";
    return num.toString();
  }

  function periodLabel(data) {
    if (data.period_start && data.period_end) {
      var s = data.period_start.split('-');
      var e = data.period_end.split('-');
      return s[2] + '/' + s[1] + ' to ' + e[2] + '/' + e[1];
    }
    return "last " + data.period_days + " days";
  }

  function fmtCost(n) {
    return "$" + Number(n).toFixed(2);
  }

  function fmtDuration(s) {
    if (!s || s === 0) return "-";
    if (s < 60) return s + "s";
    if (s < 3600) {
      const m = Math.floor(s / 60);
      return m + "m " + (s % 60) + "s";
    }
    const hh = Math.floor(s / 3600);
    const mm = Math.floor((s % 3600) / 60);
    return hh + "h " + mm + "m";
  }

  function fmtDate(isoStr) {
    const d = new Date(isoStr + "T00:00:00");
    return d.toLocaleDateString("pt-BR", { month: "short", day: "numeric" }).replace(".", "");
  }

  // Theme-aware colors — resolve CSS vars at render time
  function resolveCSSVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
  }

  // Colours per task type
  const TYPE_COLORS = {
    "Feature": "#22c55e",
    "Bug": "#ef4444",
    "QA": "#3b82f6",
    "Chore": "#a855f7",
    "Refactor": "#06b6d4",
    "Docs": "#8b5cf6",
    "Test": "#f59e0b",
    "Spike": "#ec4899",
    "Other": "#94a3b8",
  };

  function typeColor(t) {
    return TYPE_COLORS[t] || "#94a3b8";
  }

  // Profile colours
  const PROFILE_COLORS = {
    "engineer": "#22c55e",
    "qa": "#3b82f6",
    "po": "#f59e0b",
  };

  function profileColor(p) {
    return PROFILE_COLORS[p] || "#94a3b8";
  }

  // ── API fetch ────────────────────────────────────────────────────

  const fetchJSON = SDK.fetchJSON || window.fetchJSON;
  async function fetchStats(queryString) {
    try {
      return await fetchJSON("/api/plugins/hermes-kanban-insights/stats" + queryString);
    } catch (e) {
      return { error: e.message };
    }
  }

  // ── Bar component ────────────────────────────────────────────────

  function Bar({ value, max, color, label, right }) {
    const pct = max > 0 ? Math.max(2, (value / max) * 100) : 0;
    return h("div", { style: { display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" } },
      label ? h("span", { style: { width: "80px", fontSize: "12px", color: "var(--midground)", textAlign: "right" } }, label) : null,
      h("div", { style: { flex: 1, height: "20px", background: "rgba(128,128,128,0.1)", borderRadius: "4px", overflow: "hidden" } },
        h("div", { style: { width: pct + "%", height: "100%", background: color, borderRadius: "4px", transition: "width 0.3s" } })
      ),
      right ? h("span", { style: { width: "80px", fontSize: "12px", color: "var(--midground)", textAlign: "left", fontVariantNumeric: "tabular-nums" } }, right) : null
    );
  }

  // ── Summary Card ─────────────────────────────────────────────────

  function SummaryCard({ title, value, subtitle, color }) {
    return h("div", {
      style: {
        background: "rgba(128,128,128,0.04)",
        border: "1px solid rgba(128,128,128,0.15)",
        borderRadius: "8px",
        padding: "16px",
        textAlign: "center",
      }
    },
      h("div", { style: { fontSize: "11px", color: "var(--midground)", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.05em" } }, title),
      h("div", { style: { fontSize: "24px", fontWeight: "600", color: color || "var(--midground)" } }, value),
      subtitle ? h("div", { style: { fontSize: "11px", color: "var(--foreground)", marginTop: "4px" } }, subtitle) : null
    );
  }

  // ── Chart.js Daily Line Chart ────────────────────────────────────

  function DailyChart({ days: chartDays }) {
    var canvasRef = useRef(null);
    var chartRef = useRef(null);

    useEffect(function () {
      onChartJsReady(function (Chart) {
        if (!canvasRef.current) return;

        var ctx = canvasRef.current.getContext("2d");
        var textColor = resolveCSSVar("--midground") || "#e5e7eb";
        var gridColor = resolveCSSVar("--foreground") || "rgba(128,128,128,0.15)";

        var labels = chartDays.map(function (d) { return fmtDate(d.day); });
        var costData = chartDays.map(function (d) { return d.cost; });
        var tokenData = chartDays.map(function (d) { return d.input_tokens + d.output_tokens; });

        // Task run annotations for tooltip
        var taskLabels = chartDays.map(function (d) {
          return d.task_runs > 0 ? d.task_completed + "/" + d.task_runs + " runs" : "";
        });

        if (chartRef.current) {
          chartRef.current.destroy();
        }

        chartRef.current = new Chart(ctx, {
          type: "line",
          data: {
            labels: labels,
            datasets: [
              {
                label: "Cost ($)",
                data: costData,
                borderColor: "#f59e0b",
                backgroundColor: "rgba(245, 158, 11, 0.1)",
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointHoverRadius: 6,
                pointBackgroundColor: chartDays.map(function (d) { return d.cost > 0 ? "#f59e0b" : "rgba(128,128,128,0.3)"; }),
                yAxisID: "y",
              },
              {
                label: "Tokens",
                data: tokenData,
                borderColor: "#3b82f6",
                backgroundColor: "rgba(59, 130, 246, 0.1)",
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointHoverRadius: 6,
                pointBackgroundColor: chartDays.map(function (d) { return tokenData[chartDays.indexOf(d)] > 0 ? "#3b82f6" : "rgba(128,128,128,0.3)"; }),
                yAxisID: "y1",
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 3.5,
            interaction: { mode: "index", intersect: false },
            plugins: {
              legend: {
                position: "bottom",
                labels: { color: textColor, usePointStyle: true, padding: 16, font: { size: 11 } },
              },
              tooltip: {
                backgroundColor: "rgba(0,0,0,0.8)",
                titleColor: "#fff",
                bodyColor: "#ddd",
                padding: 10,
                callbacks: {
                  afterBody: function (items) {
                    var idx = items[0].dataIndex;
                    return taskLabels[idx] || "";
                  },
                },
              },
            },
            scales: {
              x: {
                ticks: { color: textColor, font: { size: 10 } },
                grid: { color: gridColor },
              },
              y: {
                type: "linear",
                display: true,
                position: "left",
                title: { display: true, text: "Cost ($)", color: "#f59e0b", font: { size: 10 } },
                ticks: { color: textColor, font: { size: 10 }, callback: function (v) { return "$" + v.toFixed(2); } },
                grid: { color: gridColor },
              },
              y1: {
                type: "linear",
                display: true,
                position: "right",
                title: { display: true, text: "Tokens", color: "#3b82f6", font: { size: 10 } },
                ticks: { color: textColor, font: { size: 10 }, callback: function (v) { return fmt(v); } },
                grid: { drawOnChartArea: false },
              },
            },
          },
        });
      });

      return function () {
        if (chartRef.current) {
          chartRef.current.destroy();
          chartRef.current = null;
        }
      };
    }, [chartDays]);

    return h("div", { style: { position: "relative" } },
      h("canvas", { ref: canvasRef })
    );
  }

  // ── Main Component ───────────────────────────────────────────────

  function UsageReport() {
    // Read initial state from URL params
    var urlParams = new URLSearchParams(window.location.search);
    var initialDays = parseInt(urlParams.get('days')) || 7;
    var initialStart = urlParams.get('start') || "";
    var initialEnd = urlParams.get('end') || "";
    // If URL has start/end, activate custom mode
    if (initialStart && initialEnd) initialDays = 0;

    const [days, setDays] = useState(initialDays);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [customStart, setCustomStart] = useState(initialStart);
    const [customEnd, setCustomEnd] = useState(initialEnd);

    // Sync state to URL
    function syncURL(d, s, e) {
      var params = new URLSearchParams();
      if (d > 0) {
        params.set('days', d);
      } else if (s && e) {
        params.set('start', s);
        params.set('end', e);
      }
      var qs = params.toString();
      var newUrl = window.location.pathname + (qs ? '?' + qs : '');
      window.history.replaceState(null, '', newUrl);
    }

    useEffect(function () {
      setLoading(true);
      setError(null);
      var url;
      if (days === 0 && customStart && customEnd) {
        url = "?start=" + customStart + "&end=" + customEnd;
      } else {
        url = "?days=" + days;
      }
      fetchStats(url).then(function (d) {
        if (d.error) {
          setError(d.error);
        } else {
          setData(d);
        }
        setLoading(false);
      });
    }, [days, customStart, customEnd]);

    function setPreset(d) {
      setDays(d);
      setCustomStart("");
      setCustomEnd("");
      syncURL(d, "", "");
    }

    function setCustom() {
      if (customStart && customEnd) {
        if (customStart <= customEnd) {
          setDays(0);
          syncURL(0, customStart, customEnd);
        }
      }
    }

    // ── Loading ──────────────────────────────────────────────────
    if (loading) {
      return h("div", { style: { padding: "40px", textAlign: "center", color: "var(--foreground)" } },
        "Loading usage data..."
      );
    }

    // ── Error ────────────────────────────────────────────────────
    if (error) {
      return h("div", { style: { padding: "40px", textAlign: "center", color: "#ef4444" } },
        h("p", {}, "Failed to load usage data"),
        h("p", { style: { fontSize: "12px", marginTop: "8px", color: "var(--midground)" } }, error)
      );
    }

    if (!data) return null;
      function MetricCard({ color, name, mainValue, subValue, barValue, barMax, children }) {
        return h("div", {
          style: {
            background: "rgba(128,128,128,0.03)",
            border: "1px solid rgba(128,128,128,0.1)",
            borderRadius: "8px",
            padding: "12px",
            marginBottom: "8px",
          }
        },
          // Header
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" } },
            h("div", { style: { display: "flex", alignItems: "center", gap: "8px" } },
              h("span", { style: { width: "12px", height: "12px", borderRadius: "3px", background: color, display: "inline-block" } }),
              h("span", { style: { fontSize: "14px", fontWeight: "500", color: "var(--midground)" } }, name),
            ),
            h("span", { style: { fontSize: "12px", color: "var(--midground)" } }, mainValue),
          ),
          // Bar
          h(Bar, { value: barValue, max: barMax, color: color }),
          // Sub-counters
          children ? h("div", { style: { marginTop: "6px", display: "flex", flexWrap: "wrap", gap: "4px" } }, children) : null,
        );
      }

      function SubBadge({ color, label }) {
        return h("span", {
          style: { padding: "2px 8px", borderRadius: "4px", fontSize: "11px", background: color + "22", color: color }
        }, label);
      }

    return h("div", { style: { padding: "24px" } },

      // ── Header with period selector ──────────────────────────
      h("div", { style: { marginBottom: "24px" } },
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" } },
          h("h1", { style: { fontSize: "18px", fontWeight: "600", color: "var(--midground)", margin: 0 } }, "Kanban Insights"),
          h("div", { style: { display: "flex", gap: "4px", background: "rgba(128,128,128,0.08)", borderRadius: "6px", padding: "2px", alignItems: "center" } },
            [7, 14, 30].map(function (d) {
              return h("button", {
                key: d,
                onClick: function () { setPreset(d); },
                style: {
                  padding: "4px 10px",
                  borderRadius: "4px",
                  border: "none",
                  cursor: "pointer",
                  fontSize: "12px",
                  fontWeight: days === d ? "600" : "400",
                  background: days === d ? "rgba(128,128,128,0.15)" : "transparent",
                  color: days === d ? "var(--midground)" : "var(--foreground)",
                }
              }, d + "d");
            }),
            h("span", { style: { fontSize: "11px", color: "var(--foreground)", padding: "0 4px" } }, "|"),
            h("input", {
              type: "date",
              value: customStart,
              onChange: function (e) { setCustomStart(e.target.value); },
              title: "Format: YYYY-MM-DD",
              style: {
                padding: "3px 6px",
                borderRadius: "4px",
                border: "1px solid rgba(128,128,128,0.2)",
                background: "transparent",
                color: "var(--midground)",
                fontSize: "11px",
                width: "130px",
                cursor: "pointer",
              }
            }),
            h("span", { style: { fontSize: "11px", color: "var(--foreground)" } }, "\u2192"),
            h("input", {
              type: "date",
              value: customEnd,
              onChange: function (e) { setCustomEnd(e.target.value); },
              title: "Format: YYYY-MM-DD",
              style: {
                padding: "3px 6px",
                borderRadius: "4px",
                border: "1px solid rgba(128,128,128,0.2)",
                background: "transparent",
                color: "var(--midground)",
                fontSize: "11px",
                width: "130px",
                cursor: "pointer",
              }
            }),
            h("button", {
              onClick: setCustom,
              style: {
                padding: "4px 8px",
                borderRadius: "4px",
                border: "none",
                cursor: "pointer",
                fontSize: "11px",
                fontWeight: "500",
                background: days === 0 ? "rgba(59,130,246,0.2)" : "rgba(128,128,128,0.1)",
                color: days === 0 ? "#3b82f6" : "var(--foreground)",
              }
            }, "Go"),
          )
        ),
        days === 0 && customStart && customEnd
          ? h("div", { style: { fontSize: "11px", color: "#3b82f6", marginTop: "4px" } },
              "Custom period: " + customStart.split('-').reverse().join('/') + " \u2192 " + customEnd.split('-').reverse().join('/')
            )
          : null,
      ),

      // ── Summary cards row ─────────────────────────────────────
      h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "12px", marginBottom: "24px" } },
        h(SummaryCard, { title: "Total Cost", value: fmtCost(data.tokens.total_cost),          subtitle: periodLabel(data),color: "#f59e0b" }),
        h(SummaryCard, { title: "Tokens (In+Out)", value: fmt(data.tokens.total_input_tokens + data.tokens.total_output_tokens), subtitle: fmt(data.tokens.total_input_tokens) + " in / " + fmt(data.tokens.total_output_tokens) + " out", color: "#3b82f6" }),
        h(SummaryCard, { title: "Task Runs", value: data.tasks.total_runs.toString(), subtitle: data.profiles.total_runs > 0 ? data.profiles.profiles.length + " profiles" : "", color: "#22c55e" }),
        h(SummaryCard, { title: "Sessions", value: data.tokens.total_sessions.toString(),          subtitle: periodLabel(data),color: "#a855f7" }),
      ),

      // ── Daily Activity chart (Chart.js) ───────────────────────
      data.daily && data.daily.days && data.daily.days.length > 0
        ? h("div", { style: { marginBottom: "24px" } },
            h("h2", { style: { fontSize: "14px", fontWeight: "600", color: "var(--midground)", marginBottom: "12px" } }, "Daily Activity & Cost"),
            h(DailyChart, { days: data.daily.days })
          )
        : null,

      // ── Unified Metric Card component ────────────────────────────

      // ── Data grid ────────────────────────────────────────────────
      h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "24px" } },

        // ── Time per Task Type ─────────────────────────────────────
        h("div", null,
          h("h2", { style: { fontSize: "14px", fontWeight: "600", color: "var(--midground)", marginBottom: "12px" } }, "Time per Task Type"),
          data.tasks.types && data.tasks.types.length > 0
            ? h("div", null,
                data.tasks.types.map(function (t) {
                  const maxDur = Math.max.apply(null, data.tasks.types.map(function (x) { return x.total_duration_seconds; }));
                  return h(MetricCard, {
                    key: t.type,
                    color: typeColor(t.type),
                    name: t.type,
                    mainValue: t.total_duration,
                    subValue: t.count + " runs",
                    barValue: t.total_duration_seconds,
                    barMax: maxDur,
                  },
                    h(SubBadge, { color: typeColor(t.type), label: t.count + " runs" }),
                    h(SubBadge, { color: "#22c55e", label: t.completed + " done" }),
                    t.blocked > 0 ? h(SubBadge, { color: "#ef4444", label: t.blocked + " blocked" }) : null,
                    h(SubBadge, { color: "#94a3b8", label: "avg " + t.avg_duration }),
                  );
                })
              )
            : h("p", { style: { color: "var(--foreground)", fontSize: "13px" } }, "No task data for this period."),
        ),

        // ── Token & Cost by Task Type ──────────────────────────────
        h("div", null,
          h("h2", { style: { fontSize: "14px", fontWeight: "600", color: "var(--midground)", marginBottom: "12px" } }, "Token & Cost by Task Type"),
          data.tokens.by_type && data.tokens.by_type.length > 0
            ? h("div", null,
                data.tokens.by_type.map(function (t) {
                  const maxCost = Math.max.apply(null, data.tokens.by_type.map(function (x) { return x.cost; }));
                  const tokens = t.input_tokens + t.output_tokens + t.cache_read_tokens;
                  return h(MetricCard, {
                    key: t.type,
                    color: typeColor(t.type),
                    name: t.type,
                    mainValue: fmtCost(t.cost),
                    subValue: fmt(tokens) + " tok",
                    barValue: t.cost,
                    barMax: maxCost,
                  },
                    h(SubBadge, { color: typeColor(t.type), label: fmtCost(t.cost) }),
                    h(SubBadge, { color: "#3b82f6", label: fmt(tokens) + " tok" }),
                    h(SubBadge, { color: "#94a3b8", label: t.runs + " runs" }),
                  );
                })
              )
            : h("p", { style: { color: "var(--foreground)", fontSize: "13px" } }, "No token data for this period."),
        ),

        // ── Time per Profile ───────────────────────────────────────
        h("div", null,
          h("h2", { style: { fontSize: "14px", fontWeight: "600", color: "var(--midground)", marginBottom: "12px" } }, "Time per Profile"),
          data.profiles.profiles && data.profiles.profiles.length > 0
            ? h("div", null,
                data.profiles.profiles.map(function (p) {
                  const maxDur = Math.max.apply(null, data.profiles.profiles.map(function (x) { return x.total_duration_seconds; }));
                  return h(MetricCard, {
                    key: p.profile,
                    color: profileColor(p.profile),
                    name: p.profile,
                    mainValue: p.total_duration,
                    subValue: p.completed + "/" + p.total_runs + " done",
                    barValue: p.total_duration_seconds,
                    barMax: maxDur,
                  },
                    h(SubBadge, { color: profileColor(p.profile), label: p.total_duration }),
                    h(SubBadge, { color: "#22c55e", label: p.completed + "/" + p.total_runs + " done" }),
                    Object.keys(p.by_type).sort().map(function (bt) {
                      return h(SubBadge, {
                        key: bt,
                        color: typeColor(bt),
                        label: bt + ": " + p.by_type[bt].count + "x " + p.by_type[bt].duration,
                      });
                    }),
                  );
                })
              )
            : h("p", { style: { color: "var(--foreground)", fontSize: "13px" } }, "No profile data for this period."),
        ),

        // ── Token & Cost by Profile ────────────────────────────────
        h("div", null,
          h("h2", { style: { fontSize: "14px", fontWeight: "600", color: "var(--midground)", marginBottom: "12px" } }, "Token & Cost by Profile"),
          data.tokens.by_profile && data.tokens.by_profile.length > 0
            ? h("div", null,
                data.tokens.by_profile.map(function (p) {
                  const maxCost = Math.max.apply(null, data.tokens.by_profile.map(function (x) { return x.cost; }));
                  return h(MetricCard, {
                    key: p.profile,
                    color: profileColor(p.profile),
                    name: p.profile,
                    mainValue: fmtCost(p.cost),
                    subValue: fmt(p.total_tokens) + " tok",
                    barValue: p.cost,
                    barMax: maxCost,
                  },
                    h(SubBadge, { color: profileColor(p.profile), label: fmtCost(p.cost) }),
                    h(SubBadge, { color: "#3b82f6", label: fmt(p.total_tokens) + " tok" }),
                    h(SubBadge, { color: "#94a3b8", label: p.runs + " runs" }),
                    p.by_type && p.by_type.length > 0 ? p.by_type.map(function (bt) {
                      return h(SubBadge, {
                        key: bt.type,
                        color: typeColor(bt.type),
                        label: bt.type + ": " + fmtCost(bt.cost) + " / " + fmt(bt.input_tokens + bt.output_tokens + bt.cache_read_tokens) + " tok",
                      });
                    }) : null,
                  );
                })
              )
            : h("p", { style: { color: "var(--foreground)", fontSize: "13px" } }, "No token data for this period."),
        ),
      ),
    );
  }

  // ── Register plugin ──────────────────────────────────────────────
  window.__HERMES_PLUGINS__.register("hermes-kanban-insights", UsageReport);
})();
