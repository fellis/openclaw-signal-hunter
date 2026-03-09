"""
Backfill matched_rules for processed_signals that are relevant but have empty matched_rules.
Uses EmbedProcessor.run_backfill_rule_match_batch (same logic as embed worker third pass).
Run on VPS: python scripts/backfill_matched_rules.py [--dry-run] [--limit N]

For production, the embed worker does this in chunks each tick (backfill_rule_match_per_tick in config),
so no timeout; one-off full run can use this script.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from core.embed_processor import EmbedProcessor
from core.orchestrator import load_rules
from storage.config_manager import ConfigManager
from storage.postgres import PostgresStorage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill matched_rules for relevant signals with empty matched_rules")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB, only log")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N records")
    args = parser.parse_args()

    config = ConfigManager(ROOT / "config.json").load()
    if not config:
        log.error("config.json not found or empty")
        sys.exit(1)

    storage = PostgresStorage()
    rules = load_rules(config)
    if not rules:
        log.warning("No extraction_rules in config; backfill will assign best_rule only")

    processor = EmbedProcessor(storage, rules, config)
    batch_size = processor._embed_batch_size

    total = storage.count_relevant_empty_matched_rules()
    if total == 0:
        log.info("No relevant signals with empty matched_rules. Nothing to do.")
        return

    to_process = min(total, args.limit) if args.limit is not None else total
    log.info(
        "Backfill matched_rules: %d to process (total with empty: %d)%s",
        to_process, total, " [dry-run]" if args.dry_run else "",
    )

    start = time.monotonic()
    processed = 0
    while processed < to_process:
        chunk = min(batch_size, to_process - processed)
        n = processor.run_backfill_rule_match_batch(chunk, dry_run=args.dry_run)
        if n == 0:
            break
        processed += n
        if processed % 500 == 0 or processed >= to_process:
            log.info("Progress: %d / %d", processed, to_process)

    elapsed = time.monotonic() - start
    log.info(
        "Done. Processed=%d, elapsed=%.1fs%s",
        processed, elapsed, " (dry-run, no writes)" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
