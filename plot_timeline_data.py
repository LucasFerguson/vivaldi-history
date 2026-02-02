#!/usr/bin/env python3
"""
Generate charts from timeline_data outputs.

Outputs PNG files to timeline_data/<source>/charts (or timeline_data/merge/charts).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors


def log(msg: str) -> None:
    print(f"[timeline-charts] {msg}")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot charts from timeline_data.")
    p.add_argument(
        "--base-dir",
        default="timeline_data",
        help="Base timeline data directory.",
    )
    p.add_argument(
        "--source",
        default="merge",
        choices=["merge", "vivaldi", "chrome"],
        help="Which dataset to chart.",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Top N entries for bar charts.",
    )
    return p.parse_args(argv)


def load_daily_visits(daily_dir: Path) -> List[Dict[str, Any]]:
    visits: List[Dict[str, Any]] = []
    for path in sorted(daily_dir.glob("history_*.json")):
        payload = load_json(path)
        visits.extend(payload.get("visits", []))
    return visits


LOCAL_TZ = ZoneInfo("America/Chicago")


def parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(LOCAL_TZ)


def plot_bar(values: List[Tuple[str, int]], title: str, out_path: Path) -> None:
    labels = [v[0] for v in values]
    counts = [v[1] for v in values]
    plt.figure(figsize=(12, 7))
    plt.barh(labels[::-1], counts[::-1])
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_hourly(hourly: List[int], out_path: Path) -> None:
    plt.figure(figsize=(10, 5))
    plt.bar(list(range(24)), hourly)
    plt.xticks(list(range(24)))
    plt.title("Hourly Activity")
    plt.xlabel(f"Hour of Day ({LOCAL_TZ.key})")
    plt.ylabel("Visits")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_weekday(weekday: Dict[str, int], out_path: Path) -> None:
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    counts = [weekday.get(d, 0) for d in order]
    plt.figure(figsize=(8, 4))
    plt.bar(order, counts)
    plt.title("Day-of-Week Activity")
    plt.xlabel(f"Day ({LOCAL_TZ.key})")
    plt.ylabel("Visits")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_heatmap(visits: List[Dict[str, Any]], out_path: Path) -> None:
    # Build date x hour matrix.
    by_date_hour = defaultdict(lambda: [0] * 24)
    dates = set()
    for v in visits:
        ts = v.get("timestamp")
        if not ts:
            continue
        dt = parse_ts(ts)
        date = dt.date().isoformat()
        dates.add(date)
        by_date_hour[date][dt.hour] += 1

    date_list = sorted(dates)
    if not date_list:
        return

    matrix = [by_date_hour[d] for d in date_list]
    plt.figure(figsize=(12, max(4, len(date_list) * 0.25)))
    vmax = max(max(row) for row in matrix) if matrix else 1
    # Compress outliers for better visibility at low counts.
    norm = mcolors.PowerNorm(gamma=0.6, vmin=0, vmax=max(1, vmax))
    plt.imshow(matrix, aspect="auto", cmap="YlGnBu", norm=norm)
    plt.colorbar(label="Visits (scaled)")
    plt.yticks(range(len(date_list)), date_list)
    plt.xticks(range(24), range(24))
    plt.title(f"Activity Heatmap (Date x Hour, {LOCAL_TZ.key})")
    plt.xlabel(f"Hour of Day ({LOCAL_TZ.key})")
    plt.ylabel("Date")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_daily_trend(visits: List[Dict[str, Any]], out_path: Path) -> None:
    counts = Counter()
    for v in visits:
        ts = v.get("timestamp")
        if not ts:
            continue
        dt = parse_ts(ts)
        counts[dt.date().isoformat()] += 1

    if not counts:
        return

    dates = sorted(counts.keys())
    values = [counts[d] for d in dates]
    plt.figure(figsize=(12, 4))
    plt.plot(dates, values, marker="o", linewidth=1.5)
    plt.title(f"Daily Visit Trend ({LOCAL_TZ.key})")
    plt.xlabel("Date")
    plt.ylabel("Visits")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    base_dir = Path(args.base_dir)
    source_dir = base_dir / args.source
    charts_dir = source_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    daily_dir = source_dir / "daily"
    if not daily_dir.exists():
        log(f"Daily directory missing: {daily_dir}")
        return 2

    log("Loading daily visits...")
    visits = load_daily_visits(daily_dir)

    # Use aggregate file if present for fast top-N lookups.
    aggregate_path = source_dir / "aggregate_merged.json"
    if not aggregate_path.exists():
        aggregate_path = source_dir / "aggregate_3weeks.json"

    aggregate = load_json(aggregate_path) if aggregate_path.exists() else {}

    log("Plotting top domains...")
    top_domains = aggregate.get("top_domains", [])
    if top_domains:
        values = [(d["domain"], d["visit_count"]) for d in top_domains[: args.top_n]]
        plot_bar(values, "Top Domains", charts_dir / "top_domains.png")

    log("Plotting top URLs...")
    top_urls = aggregate.get("top_urls", [])
    if top_urls:
        values = [
            (u["url"], u["visit_count"]) for u in top_urls[: args.top_n]
        ]
        plot_bar(values, "Top URLs", charts_dir / "top_urls.png")

    log("Plotting hourly distribution...")
    if "hourly_distribution" in aggregate:
        plot_hourly(aggregate["hourly_distribution"], charts_dir / "hourly.png")
    else:
        hourly = Counter(parse_ts(v["timestamp"]).hour for v in visits if v.get("timestamp"))
        plot_hourly([hourly.get(h, 0) for h in range(24)], charts_dir / "hourly.png")

    log("Plotting weekday distribution...")
    if "weekday_distribution" in aggregate:
        plot_weekday(aggregate["weekday_distribution"], charts_dir / "weekday.png")
    else:
        weekday = Counter(parse_ts(v["timestamp"]).strftime("%a") for v in visits if v.get("timestamp"))
        plot_weekday(dict(weekday), charts_dir / "weekday.png")

    log("Plotting heatmap...")
    plot_heatmap(visits, charts_dir / "heatmap.png")

    log("Plotting daily trend...")
    plot_daily_trend(visits, charts_dir / "daily_trend.png")

    log(f"Charts written to {charts_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
