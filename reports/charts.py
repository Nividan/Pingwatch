"""
reports/charts.py — Render Matplotlib charts as base64 data URIs.

Charts are generated with the 'Agg' backend (no GUI required) and returned
as inline <img src='data:image/png;base64,...'> strings so the WeasyPrint
HTML renderer can embed them directly without file I/O.

All public functions accept a data payload and return a data URI str.
Failures return an empty string — templates gracefully omit missing charts.
"""

import base64
import datetime
import io

import matplotlib
matplotlib.use("Agg")   # must precede any pyplot import
import matplotlib.pyplot as plt

from core.logger import log


# ── Shared styling ─────────────────────────────────────────────────────

_PRINT_BG    = "#ffffff"
_PRINT_FG    = "#1f2328"
_PRINT_MUTED = "#57606a"
_PRINT_GRID  = "#e4e7ec"
_PRINT_ACC   = "#0969da"
_PRINT_WARN  = "#9a6700"
_PRINT_DOWN  = "#cf222e"
_PRINT_UP    = "#1a7f37"


def _style_axes(ax):
    ax.set_facecolor(_PRINT_BG)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_PRINT_MUTED)
    ax.spines["bottom"].set_color(_PRINT_MUTED)
    ax.tick_params(colors=_PRINT_MUTED, labelsize=8)
    ax.yaxis.label.set_color(_PRINT_FG)
    ax.xaxis.label.set_color(_PRINT_FG)
    ax.title.set_color(_PRINT_FG)
    ax.grid(True, axis="y", color=_PRINT_GRID, linewidth=0.5)
    ax.set_axisbelow(True)


def _encode(fig) -> str:
    buf = io.BytesIO()
    fig.patch.set_facecolor(_PRINT_BG)
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=_PRINT_BG)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ── Public chart builders ──────────────────────────────────────────────

def availability_trend(avail_hourly: list) -> str:
    """
    Line chart of hourly availability % over the period.

    avail_hourly — output of db_load_availability(): [{ts, pct, up, total}]
    """
    if not avail_hourly:
        return ""
    try:
        xs = [datetime.datetime.fromtimestamp(r["ts"]) for r in avail_hourly]
        ys = [r.get("pct") or 0 for r in avail_hourly]
        fig, ax = plt.subplots(figsize=(8, 2.4))
        _style_axes(ax)
        ax.plot(xs, ys, color=_PRINT_ACC, linewidth=1.2)
        ax.fill_between(xs, ys, 100, color=_PRINT_DOWN, alpha=0.08)
        ax.set_ylim(min(80, min(ys) - 1), 101)
        ax.set_ylabel("Availability %")
        ax.set_title("Hourly Availability", fontsize=10, loc="left", pad=8)
        fig.autofmt_xdate(rotation=0)
        return _encode(fig)
    except Exception as e:
        log.error(f"reports.charts availability_trend error: {e}")
        return ""


def severity_donut(severity: dict) -> str:
    """
    Donut chart of incident severity distribution.
    severity — {'crit': N, 'warn': N, 'resolved': N, 'total': N}
    """
    if not severity or not severity.get("total"):
        return ""
    try:
        labels, values, colors = [], [], []
        if severity.get("crit"):
            labels.append("Critical"); values.append(severity["crit"]); colors.append(_PRINT_DOWN)
        if severity.get("warn"):
            labels.append("Warning");  values.append(severity["warn"]); colors.append(_PRINT_WARN)
        other = max(0, severity.get("total", 0) - severity.get("crit", 0) - severity.get("warn", 0))
        if other:
            labels.append("Other"); values.append(other); colors.append(_PRINT_MUTED)
        fig, ax = plt.subplots(figsize=(3.2, 3.2))
        ax.set_facecolor(_PRINT_BG)
        wedges, _texts = ax.pie(
            values, colors=colors, startangle=90,
            wedgeprops=dict(width=0.38, edgecolor=_PRINT_BG, linewidth=2),
        )
        ax.text(0, 0.05, str(severity.get("total", 0)),
                ha="center", va="center", fontsize=22, color=_PRINT_FG, fontweight="bold")
        ax.text(0, -0.18, "incidents", ha="center", va="center",
                fontsize=9, color=_PRINT_MUTED)
        ax.legend(wedges, labels, loc="upper center",
                  bbox_to_anchor=(0.5, 0.05), ncol=len(labels),
                  frameon=False, fontsize=8, labelcolor=_PRINT_FG)
        ax.axis("equal")
        return _encode(fig)
    except Exception as e:
        log.error(f"reports.charts severity_donut error: {e}")
        return ""


def top_bar(rows: list, value_key: str, label_key: str,
            title: str = "", color: str = None) -> str:
    """Horizontal bar chart — top-N."""
    if not rows:
        return ""
    try:
        color = color or _PRINT_ACC
        labels = [str(r.get(label_key, ""))[:28] for r in rows][::-1]
        values = [r.get(value_key, 0) for r in rows][::-1]
        fig, ax = plt.subplots(figsize=(7, max(1.8, 0.35 * len(rows) + 1)))
        _style_axes(ax)
        ax.barh(labels, values, color=color, edgecolor=color)
        ax.set_title(title, fontsize=10, loc="left", pad=8)
        ax.grid(True, axis="x", color=_PRINT_GRID, linewidth=0.5)
        ax.grid(False, axis="y")
        for i, v in enumerate(values):
            ax.text(v, i, f"  {v}", va="center", fontsize=8, color=_PRINT_FG)
        return _encode(fig)
    except Exception as e:
        log.error(f"reports.charts top_bar error: {e}")
        return ""


def incident_timeline(flaps: list, start_ts: float, end_ts: float) -> str:
    """Scatter strip showing when incidents occurred across the period."""
    if not flaps:
        return ""
    try:
        xs_crit, xs_warn, xs_other = [], [], []
        for f in flaps:
            d = (f.get("direction") or "").lower()
            dt = datetime.datetime.fromtimestamp(f["ts"])
            if d in ("down", "threshold_crit"): xs_crit.append(dt)
            elif d in ("threshold_warn", "anomaly_warn"): xs_warn.append(dt)
            else: xs_other.append(dt)
        fig, ax = plt.subplots(figsize=(8, 1.5))
        _style_axes(ax)
        if xs_crit:
            ax.scatter(xs_crit, [3] * len(xs_crit), color=_PRINT_DOWN,
                       s=28, marker="|", linewidth=2, label="Critical")
        if xs_warn:
            ax.scatter(xs_warn, [2] * len(xs_warn), color=_PRINT_WARN,
                       s=28, marker="|", linewidth=2, label="Warning")
        if xs_other:
            ax.scatter(xs_other, [1] * len(xs_other), color=_PRINT_MUTED,
                       s=22, marker="|", linewidth=1, label="Other")
        ax.set_yticks([1, 2, 3])
        ax.set_yticklabels(["Other", "Warn", "Crit"])
        ax.set_ylim(0.5, 3.5)
        ax.set_xlim(datetime.datetime.fromtimestamp(start_ts),
                    datetime.datetime.fromtimestamp(end_ts))
        ax.set_title("Incident timeline", fontsize=10, loc="left", pad=8)
        fig.autofmt_xdate(rotation=0)
        return _encode(fig)
    except Exception as e:
        log.error(f"reports.charts incident_timeline error: {e}")
        return ""


def latency_percentile_bar(latency: list, n: int = 10) -> str:
    """Grouped bar chart of top-N sensors by p95 latency."""
    if not latency:
        return ""
    try:
        rows = latency[:n]
        labels = [f"{r['dname'][:14]}·{r['sname'][:14]}" for r in rows][::-1]
        p50 = [r.get("p50") or 0 for r in rows][::-1]
        p95 = [r.get("p95") or 0 for r in rows][::-1]
        p99 = [r.get("p99") or 0 for r in rows][::-1]
        fig, ax = plt.subplots(figsize=(7, max(2, 0.4 * len(rows) + 1)))
        _style_axes(ax)
        import numpy as np
        y = np.arange(len(labels))
        h = 0.26
        ax.barh(y - h, p50, h, color=_PRINT_UP,   label="p50")
        ax.barh(y,     p95, h, color=_PRINT_ACC,  label="p95")
        ax.barh(y + h, p99, h, color=_PRINT_DOWN, label="p99")
        ax.set_yticks(y); ax.set_yticklabels(labels)
        ax.set_xlabel("Latency (ms)")
        ax.legend(loc="lower right", frameon=False, fontsize=8)
        ax.set_title("Latency percentiles — top sensors", fontsize=10, loc="left", pad=8)
        return _encode(fig)
    except Exception as e:
        log.error(f"reports.charts latency_percentile_bar error: {e}")
        return ""
