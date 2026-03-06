"""
Migration: add signal_translations table if it does not exist.
Run manually against a live database:
  python scripts/migrate_add_translations.py
"""
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://signal:signal@localhost:5433/signal_hunter")

DDL = """
CREATE TABLE IF NOT EXISTS signal_translations (
    signal_id   UUID    NOT NULL REFERENCES raw_signals(id) ON DELETE CASCADE,
    lang        TEXT    NOT NULL,
    field       TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (signal_id, lang, field)
);
CREATE INDEX IF NOT EXISTS idx_signal_translations_sid  ON signal_translations (signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_translations_lang ON signal_translations (lang, field);
"""

def main() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
        print("Migration applied successfully.")
    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
