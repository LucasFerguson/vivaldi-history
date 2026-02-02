# Vivaldi History Exporter

Exports Vivaldi (Chromium) history into daily JSON files plus aggregate views for analysis and LLM input.

## Requirements
- Python 3.8+
- Vivaldi history DB at:
  `/mnt/c/Users/lucas/AppData/Local/Vivaldi/User Data/Default/History`

## Usage
```bash
python3 export_vivaldi_history.py --weeks 3 --output-dir timeline_data
```

Optional arguments:
- `--weeks`: number of weeks to export (default: 3)
- `--output-dir`: output directory for JSON files (default: `timeline_data`)
- `--db-path`: override the History DB path

## Output Structure
```
timeline_data/
├── daily/
│   ├── history_YYYY-MM-DD.json
│   └── ...
├── aggregate_3weeks.json
└── llm_input.json
```

## Notes
- The script copies the History database to `/tmp/History_copy` before querying to avoid locks.
- Chrome timestamps are converted to ISO 8601 (UTC, `Z` suffix).
- Transition types are decoded from the Chromium visit transition bitmask.
- Search queries are extracted for common engines (Google, Bing, DuckDuckGo, Yahoo, Brave).
