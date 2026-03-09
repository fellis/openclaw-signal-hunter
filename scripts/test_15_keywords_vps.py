"""
Test 15 keywords from VPS DB: count signals and check classification readiness.
Report is written to a file next to this script (JSON + optional TXT).
Run from the environment where all dependencies are installed:
  python scripts/test_15_keywords_vps.py
  python scripts/test_15_keywords_vps.py --collect
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from core.orchestrator import Orchestrator
from storage.config_manager import ConfigManager
from storage.postgres import PostgresStorage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
REPORT_JSON = SCRIPT_DIR / "test_15_keywords_vps_report.json"
REPORT_TXT = SCRIPT_DIR / "test_15_keywords_vps_report.txt"

KEYWORDS_LIMIT = 15


def _ready_for_classification(row: dict) -> bool:
    """Signal is ready if it has dedup_key and at least one of title/body non-empty."""
    dedup = (row.get("dedup_key") or "").strip()
    title = (row.get("title") or "").strip()
    body = (row.get("body") or "").strip()
    return bool(dedup and (title or body))


def run(
    storage: PostgresStorage,
    config: dict,
    do_collect: bool,
) -> dict:
    keywords_all = storage.list_keyword_profiles()
    selected = keywords_all[:KEYWORDS_LIMIT]
    if not selected:
        return {
            "keywords_selected": [],
            "note": "No keywords in DB (keyword_profiles empty).",
            "per_keyword": {},
            "summary": {"total_signals": 0, "ready_for_classification": 0, "no_text": 0},
        }

    if do_collect:
        log.info("Running collect for %d keywords: %s", len(selected), selected)
        orch = Orchestrator(config, storage)
        orch.collect(keywords=selected)

    counts = storage.count_raw_signals_for_keywords(selected)
    signals = storage.fetch_raw_signals_for_keywords(selected)

    # Per-keyword: each signal can have multiple keywords in extra; attribute readiness to each
    per_kw: dict[str, dict] = {kw: {"total": counts.get(kw, 0), "ready": 0, "no_text": 0} for kw in selected}
    ready_total = 0
    no_text_total = 0

    for row in signals:
        extra = row.get("extra") or {}
        kw_list = extra.get("keywords") if isinstance(extra, dict) else []
        if not isinstance(kw_list, list):
            kw_list = []
        ready = _ready_for_classification(row)
        if ready:
            ready_total += 1
        else:
            no_text_total += 1
        for kw in kw_list:
            if kw in per_kw:
                if ready:
                    per_kw[kw]["ready"] += 1
                else:
                    per_kw[kw]["no_text"] += 1

    summary = {
        "total_signals": len(signals),
        "ready_for_classification": ready_total,
        "no_text": no_text_total,
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "keywords_selected": selected,
        "per_keyword": per_kw,
        "summary": summary,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test 15 keywords: count signals and check classification readiness. Writes report to file."
    )
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Run collection for these 15 keywords before reporting",
    )
    args = parser.parse_args()

    config = ConfigManager(ROOT / "config.json").load()
    storage = PostgresStorage()

    try:
        report = run(storage, config or {}, args.collect)
    except Exception as e:
        log.exception("test_15_keywords_vps failed: %s", e)
        sys.exit(1)

    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    lines = [
        "Test 15 keywords report",
        "Generated: " + report.get("generated_at", ""),
        "",
        "Keywords: " + ", ".join(report.get("keywords_selected", [])),
        "",
        "Per keyword:",
    ]
    for kw, data in report.get("per_keyword", {}).items():
        lines.append(f"  {kw}: total={data.get('total', 0)} ready={data.get('ready', 0)} no_text={data.get('no_text', 0)}")
    lines.append("")
    s = report.get("summary", {})
    lines.append(f"Summary: total_signals={s.get('total_signals', 0)} ready_for_classification={s.get('ready_for_classification', 0)} no_text={s.get('no_text', 0)}")

    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Report written to {REPORT_JSON}", flush=True)
    print(f"Report written to {REPORT_TXT}", flush=True)


if __name__ == "__main__":
    main()
