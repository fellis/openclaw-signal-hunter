"""
One-time migration: clean existing raw_signal bodies in the database.

Applies the same clean_body() logic used at collection time (storage/text_cleaner.py)
to all rows already stored with raw HTML/code content.

Usage (on VPS from skill directory):
    .venv/bin/python scripts/clean_existing_bodies.py [--dry-run] [--batch-size 500]

Options:
    --dry-run       Report what would change without writing to DB
    --batch-size N  Rows per SELECT batch (default: 500)

The script is idempotent - safe to re-run multiple times.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Allow imports from skill root
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
import psycopg2.extras

from storage.text_cleaner import clean_body


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clean existing raw_signal bodies")
    p.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    p.add_argument("--batch-size", type=int, default=500, metavar="N")
    return p.parse_args()


def run(args: argparse.Namespace) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip()
                    break
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Count total rows upfront
    cur.execute("SELECT COUNT(*) FROM raw_signals")
    total = cur.fetchone()[0]
    print(f"Total rows in raw_signals: {total}")

    processed = 0
    changed = 0
    offset = 0
    t0 = time.time()

    while True:
        cur.execute(
            "SELECT id, body FROM raw_signals ORDER BY id OFFSET %s LIMIT %s",
            (offset, args.batch_size),
        )
        rows = cur.fetchall()
        if not rows:
            break

        updates: list[tuple[str, str]] = []
        for row in rows:
            raw = row["body"] or ""
            cleaned = clean_body(raw)
            if cleaned != raw:
                updates.append((cleaned, row["id"]))

        if updates and not args.dry_run:
            psycopg2.extras.execute_batch(
                cur,
                "UPDATE raw_signals SET body = %s WHERE id = %s",
                updates,
            )
            conn.commit()

        changed += len(updates)
        processed += len(rows)
        offset += args.batch_size

        elapsed = time.time() - t0
        print(
            f"  [{processed}/{total}] changed {changed} rows "
            f"(this batch: {len(updates)}) | {elapsed:.1f}s elapsed"
        )

    conn.close()

    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{mode}Done: {processed} rows scanned, {changed} rows cleaned")


if __name__ == "__main__":
    run(parse_args())
