#!/usr/bin/env python3
"""
Merge per-browser timeline data into a single merged view.

Reads:
  timeline_data/vivaldi/
  timeline_data/chrome/

Writes:
  timeline_data/merge/
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List


def log(msg: str) -> None:
    print(f"[merge-timeline] {msg}")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def merge_daily(
    sources: Dict[str, Path],
    merge_dir: Path,
) -> List[Dict[str, Any]]:
    # date -> list of visit records
    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for source_name, source_dir in sources.items():
        daily_dir = source_dir / "daily"
        if not daily_dir.exists():
            continue
        for path in sorted(daily_dir.glob("history_*.json")):
            payload = load_json(path)
            date = payload.get("date")
            visits = payload.get("visits", [])
            for v in visits:
                v["source"] = source_name
                by_date[date].append(v)

    merge_daily_dir = merge_dir / "daily"
    merge_daily_dir.mkdir(parents=True, exist_ok=True)

    daily_summary: List[Dict[str, Any]] = []
    for date, visits in sorted(by_date.items()):
        unique_urls = len({v.get("url") for v in visits if v.get("url")})
        daily_summary.append(
            {"date": date, "visits": len(visits), "unique_urls": unique_urls}
        )
        payload = {
            "date": date,
            "total_visits": len(visits),
            "unique_urls": unique_urls,
            "visits": visits,
        }
        (merge_daily_dir / f"history_{date}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    return daily_summary


def merge_aggregate(
    sources: Dict[str, Path],
    merge_dir: Path,
    daily_summary: List[Dict[str, Any]],
) -> None:
    total_visits = 0
    unique_urls = set()
    domain_counts = Counter()
    url_counts = Counter()
    url_to_title: Dict[str, str] = {}
    hourly = [0] * 24
    weekday_counts = Counter()
    top_search_queries = Counter()
    periods = []

    for source_dir in sources.values():
        aggregate_files = sorted(source_dir.glob("aggregate_*weeks.json"))
        for path in aggregate_files:
            data = load_json(path)
            total_visits += data.get("total_visits", 0)
            periods.append(data.get("period"))
            for item in data.get("top_domains", []):
                domain_counts[item.get("domain")] += item.get("visit_count", 0)
            for item in data.get("top_urls", []):
                url = item.get("url")
                if url:
                    url_counts[url] += item.get("visit_count", 0)
                    if item.get("title"):
                        url_to_title.setdefault(url, item["title"])
                    unique_urls.add(url)
            for i, v in enumerate(data.get("hourly_distribution", [])):
                if i < 24:
                    hourly[i] += v
            for day, count in data.get("weekday_distribution", {}).items():
                weekday_counts[day] += count
            for item in data.get("top_search_queries", []):
                query = item.get("query")
                if query:
                    top_search_queries[query] += item.get("count", 0)

    top_domains = [
        {"domain": d, "visit_count": c} for d, c in domain_counts.most_common(20)
    ]
    top_urls = []
    for url, count in url_counts.most_common(50):
        top_urls.append({"url": url, "visit_count": count, "title": url_to_title.get(url)})

    merged_aggregate = {
        "periods": [p for p in periods if p],
        "total_visits": total_visits,
        "unique_urls": len(unique_urls),
        "top_domains": top_domains,
        "top_urls": top_urls,
        "daily_summary": daily_summary,
        "hourly_distribution": hourly,
        "weekday_distribution": dict(weekday_counts),
        "top_search_queries": [
            {"query": q, "count": c} for q, c in top_search_queries.most_common(50)
        ],
    }

    (merge_dir / "aggregate_merged.json").write_text(
        json.dumps(merged_aggregate, indent=2), encoding="utf-8"
    )


def merge_llm_input(
    sources: Dict[str, Path],
    merge_dir: Path,
) -> None:
    # date -> urls/titles/domains
    by_date: Dict[str, Dict[str, set]] = defaultdict(
        lambda: {"urls": set(), "titles": set(), "domains": Counter()}
    )
    for source_dir in sources.values():
        llm_path = source_dir / "llm_input.json"
        if not llm_path.exists():
            continue
        data = load_json(llm_path)
        for entry in data.get("daily_summaries", []):
            date = entry.get("date")
            if not date:
                continue
            by_date[date]["urls"].update(entry.get("urls_visited", []))
            by_date[date]["titles"].update(entry.get("titles", []))
            for domain in entry.get("top_domains", []):
                by_date[date]["domains"][domain] += 1

    merged = {"daily_summaries": []}
    for date, data in sorted(by_date.items()):
        top_domains = [
            d for d, _ in data["domains"].most_common(10)
        ]
        merged["daily_summaries"].append(
            {
                "date": date,
                "urls_visited": sorted(data["urls"]),
                "titles": sorted(data["titles"]),
                "top_domains": top_domains,
            }
        )

    (merge_dir / "llm_input.json").write_text(
        json.dumps(merged, indent=2), encoding="utf-8"
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Merge timeline_data from multiple browsers.")
    p.add_argument(
        "--base-dir",
        default="timeline_data",
        help="Base timeline data directory.",
    )
    p.add_argument(
        "--sources",
        default="vivaldi,chrome",
        help="Comma-separated list of source subdirectories.",
    )
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    base_dir = Path(args.base_dir)
    sources = {
        name: base_dir / name
        for name in [s.strip() for s in args.sources.split(",") if s.strip()]
    }
    merge_dir = base_dir / "merge"
    merge_dir.mkdir(parents=True, exist_ok=True)

    log("Merging daily files...")
    daily_summary = merge_daily(sources, merge_dir)
    log("Merging aggregate data...")
    merge_aggregate(sources, merge_dir, daily_summary)
    log("Merging LLM input...")
    merge_llm_input(sources, merge_dir)
    log("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
