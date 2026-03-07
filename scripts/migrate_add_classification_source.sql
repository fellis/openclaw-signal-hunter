-- Add classification_source to processed_signals for report metrics:
-- 'embedding' = decided by embed worker, 'llm' = decided by LLM (borderline).
-- Run once: psql $DATABASE_URL -f scripts/migrate_add_classification_source.sql

ALTER TABLE processed_signals
  ADD COLUMN IF NOT EXISTS classification_source TEXT NOT NULL DEFAULT 'embedding'
  CHECK (classification_source IN ('embedding', 'llm'));

COMMENT ON COLUMN processed_signals.classification_source IS 'Who made the final relevance decision: embedding (embed worker) or llm (borderline resolved by LLM worker).';
