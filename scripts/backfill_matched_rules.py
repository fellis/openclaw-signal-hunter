"""
Backfill matched_rules for processed_signals that are relevant but have empty matched_rules.
Uses the same rule-matching logic as the embed worker (EmbedProcessor._classify_vectors).
Run on VPS from signal-hunter root: python scripts/backfill_matched_rules.py [--dry-run] [--limit N]

Expected runtime for ~25k records: on the order of 3-10 minutes depending on embedder and DB.
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
from core.models import MatchedRule
from storage.config_manager import ConfigManager
from storage.postgres import PostgresStorage
from storage.text_cleaner import strip_hn_prefix

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _build_text(row: dict, max_body_chars: int) -> str:
    """Build concatenated title+body for embedding, same as EmbedProcessor."""
    title = strip_hn_prefix((row.get("title") or "").strip())
    body = (row.get("body") or "").strip()
    if len(body) > max_body_chars:
        body = body[:max_body_chars] + "..."
    return (f"{title}\n\n{body}".strip()) if title else body


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
    from core.orchestrator import load_rules

    rules = load_rules(config)
    if not rules:
        log.warning("No extraction_rules in config; backfill will assign best_rule only")

    processor = EmbedProcessor(storage, rules, config)
    batch_size = processor._embed_batch_size
    max_body_chars = processor._max_body_chars

    total = storage.count_relevant_empty_matched_rules()
    if total == 0:
        log.info("No relevant signals with empty matched_rules. Nothing to do.")
        return

    limit = args.limit if args.limit is not None else total
    to_process = min(total, limit)
    log.info("Backfill matched_rules: %d to process (total with empty matched_rules: %d)%s", to_process, total, " [dry-run]" if args.dry_run else "")

    start = time.monotonic()
    processed = 0
    errors = 0

    offset = 0
    while processed < to_process:
        fetch_size = min(batch_size, to_process - processed)
        batch = storage.fetch_relevant_empty_matched_rules_batch(fetch_size, offset)
        if not batch:
            break

        texts = [_build_text(row, max_body_chars) for row in batch]
        try:
            vectors = processor._embed_texts(texts)
        except Exception as e:
            log.error("Embed batch failed at offset %d: %s", offset, e)
            errors += len(batch)
            offset += len(batch)
            continue

        results = processor._classify_vectors(vectors)

        for row, res in zip(batch, results):
            matched_rule_names = list(res.get("matched_rules") or [])
            if not matched_rule_names and res.get("best_rule"):
                matched_rule_names = [res["best_rule"]]
            confidence = res.get("confidence", 0.0)
            intensity = res.get("intensity", 1)
            matched_rules = [
                MatchedRule(rule_name=name, confidence=confidence, evidence="")
                for name in matched_rule_names
            ]

            if not args.dry_run:
                try:
                    storage.update_processed_signal_rule_match(
                        row["dedup_key"], matched_rules, confidence, intensity
                    )
                except Exception as e:
                    log.error("Update failed for dedup_key=%s: %s", row["dedup_key"], e)
                    errors += 1
                    continue
            processed += 1

        offset += len(batch)
        if processed % 500 == 0 or processed == to_process:
            log.info("Progress: %d / %d", processed, to_process)

    elapsed = time.monotonic() - start
    log.info(
        "Done. Processed=%d, errors=%d, elapsed=%.1fs%s",
        processed, errors, elapsed, " (dry-run, no writes)" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
