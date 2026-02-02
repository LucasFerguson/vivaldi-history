#!/usr/bin/env python3
"""
Export Vivaldi (Chromium) history into JSON files for timeline analysis.

Usage:
  python3 export_vivaldi_history.py --weeks 3 --output-dir timeline_data
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import parse_qs, urlparse


DEFAULT_DB_PATH = "/mnt/c/Users/lucas/AppData/Local/Vivaldi/User Data/Default/History"
DEFAULT_OUTPUT_DIR = "timeline_data"
TMP_DB_COPY = "/tmp/History_copy"


def log(msg: str) -> None:
    print(f"[vivaldi-history] {msg}")


def chrome_time_to_datetime(chrome_ts: int) -> datetime:
    # Chrome timestamps are microseconds since 1601-01-01 UTC.
    unix_ts = (chrome_ts / 1_000_000) - 11_644_473_600
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


def datetime_to_chrome_time(dt: datetime) -> int:
    # Reverse conversion to chrome microseconds.
    unix_ts = dt.timestamp()
    return int((unix_ts + 11_644_473_600) * 1_000_000)


def copy_database(src: str, dst: str) -> str:
    log(f"Copying database to {dst}...")
    shutil.copy2(src, dst)
    return dst


def get_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def decode_transition(transition: int) -> Tuple[str, List[str]]:
    # Lower 8 bits are the core transition type.
    core = transition & 0xFF
    core_map = {
        0: "link",
        1: "typed",
        2: "auto_bookmark",
        3: "auto_subframe",
        4: "manual_subframe",
        5: "generated",
        6: "auto_toplevel",
        7: "form_submit",
        8: "reload",
        9: "keyword",
        10: "keyword_generated",
    }
    core_name = core_map.get(core, f"unknown_{core}")

    qualifiers = []
    if transition & 0x01000000:
        qualifiers.append("chain_start")
    if transition & 0x02000000:
        qualifiers.append("chain_end")
    if transition & 0x04000000:
        qualifiers.append("client_redirect")
    if transition & 0x08000000:
        qualifiers.append("server_redirect")
    if transition & 0x10000000:
        qualifiers.append("is_redirect")
    if transition & 0x20000000:
        qualifiers.append("from_address_bar")
    if transition & 0x40000000:
        qualifiers.append("home_page")
    if transition & 0x80000000:
        qualifiers.append("user_gesture")
    return core_name, qualifiers


def extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def extract_search_query(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    engine_hosts = {
        "www.google.com": "q",
        "google.com": "q",
        "www.bing.com": "q",
        "bing.com": "q",
        "duckduckgo.com": "q",
        "www.duckduckgo.com": "q",
        "search.brave.com": "q",
        "search.yahoo.com": "p",
        "yahoo.com": "p",
    }

    if host in engine_hosts and ("search" in path or host.startswith("duckduckgo")):
        params = parse_qs(parsed.query)
        key = engine_hosts[host]
        if key in params and params[key]:
            return params[key][0]
    return None


def query_history(
    db_path: str, since_chrome_ts: int
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    log("Querying history...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    url_cols = get_table_columns(conn, "urls")
    visit_cols = get_table_columns(conn, "visits")

    select_parts = (
        [f"u.{c} AS url_{c}" for c in url_cols]
        + [f"v.{c} AS visit_{c}" for c in visit_cols]
    )
    select_sql = ", ".join(select_parts)

    sql = f"""
        SELECT {select_sql}
        FROM urls u
        JOIN visits v ON v.url = u.id
        WHERE v.visit_time >= ?
        ORDER BY v.visit_time ASC
    """

    rows = conn.execute(sql, (since_chrome_ts,)).fetchall()
    conn.close()

    log(f"Fetched {len(rows)} visit rows.")
    return [dict(r) for r in rows], url_cols, visit_cols


def build_records(
    rows: List[Dict[str, Any]], url_cols: List[str], visit_cols: List[str]
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    for r in rows:
        url = r.get("url_url") or ""
        title = r.get("url_title") or ""
        visit_time_raw = r.get("visit_visit_time") or 0
        last_visit_raw = r.get("url_last_visit_time") or 0

        visit_dt = chrome_time_to_datetime(visit_time_raw)
        last_visit_dt = chrome_time_to_datetime(last_visit_raw) if last_visit_raw else None

        transition_raw = r.get("visit_transition") or 0
        transition_name, transition_qualifiers = decode_transition(transition_raw)

        record = {
            "url": url,
            "title": title,
            "timestamp": visit_dt.isoformat().replace("+00:00", "Z"),
            "domain": extract_domain(url),
            "visit_count": r.get("url_visit_count"),
            "typed_count": r.get("url_typed_count"),
            "hidden": r.get("url_hidden"),
            "last_visit_time": last_visit_dt.isoformat().replace("+00:00", "Z")
            if last_visit_dt
            else None,
            "from_visit": r.get("visit_from_visit"),
            "transition_type": transition_name,
            "transition_qualifiers": transition_qualifiers,
            "transition_raw": transition_raw,
            "visit_id": r.get("visit_id"),
            "url_id": r.get("url_id"),
            "visit_time_raw": visit_time_raw,
            "last_visit_time_raw": last_visit_raw,
        }

        # Capture any extra metadata not already in the main record.
        extra: Dict[str, Any] = {}
        for c in url_cols:
            key = f"url_{c}"
            if key not in record and key in r:
                extra[key] = r[key]
        for c in visit_cols:
            key = f"visit_{c}"
            if key not in record and key in r:
                extra[key] = r[key]
        if extra:
            record["extra_metadata"] = extra

        records.append(record)

    return records


def group_by_date(records: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        date = r["timestamp"][:10]
        by_date[date].append(r)
    return by_date


def hourly_distribution(records: Iterable[Dict[str, Any]]) -> List[int]:
    hours = [0] * 24
    for r in records:
        dt = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        hours[dt.hour] += 1
    return hours


def weekday_distribution(records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    counts = {n: 0 for n in names}
    for r in records:
        dt = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        counts[names[dt.weekday()]] += 1
    return counts


def build_daily_files(
    by_date: Dict[str, List[Dict[str, Any]]], daily_dir: Path
) -> List[Dict[str, Any]]:
    daily_summary: List[Dict[str, Any]] = []
    for date, visits in sorted(by_date.items()):
        unique_urls = len({v["url"] for v in visits})
        daily_summary.append(
            {"date": date, "visits": len(visits), "unique_urls": unique_urls}
        )
        payload = {
            "date": date,
            "total_visits": len(visits),
            "unique_urls": unique_urls,
            "visits": visits,
        }
        daily_path = daily_dir / f"history_{date}.json"
        daily_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return daily_summary


def build_aggregate(
    records: List[Dict[str, Any]],
    by_date: Dict[str, List[Dict[str, Any]]],
    period_start: datetime,
    period_end: datetime,
) -> Dict[str, Any]:
    total_visits = len(records)
    unique_urls = len({r["url"] for r in records})

    domain_counts = Counter(r["domain"] for r in records if r["domain"])
    url_counts = Counter(r["url"] for r in records if r["url"])

    top_domains = [
        {"domain": d, "visit_count": c} for d, c in domain_counts.most_common(20)
    ]
    top_urls = []
    url_to_title = {}
    for r in records:
        if r["url"] and r["title"]:
            url_to_title.setdefault(r["url"], r["title"])
    for url, count in url_counts.most_common(50):
        top_urls.append({"url": url, "visit_count": count, "title": url_to_title.get(url)})

    daily_summary = [
        {
            "date": d,
            "visits": len(v),
            "unique_urls": len({x["url"] for x in v}),
        }
        for d, v in sorted(by_date.items())
    ]

    search_queries = Counter(
        q for q in (extract_search_query(r["url"]) for r in records) if q
    )
    top_search_queries = [
        {"query": q, "count": c} for q, c in search_queries.most_common(50)
    ]

    return {
        "period": f"{period_start.date()} to {period_end.date()}",
        "total_visits": total_visits,
        "unique_urls": unique_urls,
        "top_domains": top_domains,
        "top_urls": top_urls,
        "daily_summary": daily_summary,
        "hourly_distribution": hourly_distribution(records),
        "weekday_distribution": weekday_distribution(records),
        "top_search_queries": top_search_queries,
    }


def build_llm_input(
    by_date: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, Any]:
    daily_summaries = []
    for date, visits in sorted(by_date.items()):
        urls = list({v["url"] for v in visits if v["url"]})
        titles = list({v["title"] for v in visits if v["title"]})
        domain_counts = Counter(v["domain"] for v in visits if v["domain"])
        top_domains = [d for d, _ in domain_counts.most_common(10)]
        daily_summaries.append(
            {
                "date": date,
                "urls_visited": urls,
                "titles": titles,
                "top_domains": top_domains,
            }
        )
    return {"daily_summaries": daily_summaries}


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export Vivaldi history to JSON.")
    p.add_argument("--weeks", type=int, default=3, help="Number of weeks to export.")
    p.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for timeline data.",
    )
    p.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="Path to Vivaldi History SQLite database.",
    )
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    weeks = args.weeks
    output_dir = Path(args.output_dir)

    if weeks <= 0:
        log("Weeks must be a positive integer.")
        return 2

    if not os.path.exists(args.db_path):
        log(f"History database not found: {args.db_path}")
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    daily_dir = output_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(weeks=weeks)
    since_chrome_ts = datetime_to_chrome_time(period_start)

    copy_database(args.db_path, TMP_DB_COPY)

    rows, url_cols, visit_cols = query_history(TMP_DB_COPY, since_chrome_ts)
    records = build_records(rows, url_cols, visit_cols)

    log("Building per-day files...")
    by_date = group_by_date(records)
    build_daily_files(by_date, daily_dir)

    log("Building aggregate JSON...")
    aggregate = build_aggregate(records, by_date, period_start, period_end)
    aggregate_path = output_dir / f"aggregate_{weeks}weeks.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")

    log("Building LLM input JSON...")
    llm_input = build_llm_input(by_date)
    llm_path = output_dir / "llm_input.json"
    llm_path.write_text(json.dumps(llm_input, indent=2), encoding="utf-8")

    log("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
