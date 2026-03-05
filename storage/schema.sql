-- Signal Hunter production schema

CREATE TABLE IF NOT EXISTS raw_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dedup_key       TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT,
    body            TEXT,
    author          TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    score           INT DEFAULT 0,
    comments_count  INT DEFAULT 0,
    views_count     INT DEFAULT 0,
    tags            TEXT[] DEFAULT '{}',
    parent_url      TEXT,
    extra           JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_raw_signals_collected_at ON raw_signals (collected_at);
CREATE INDEX IF NOT EXISTS idx_raw_signals_source ON raw_signals (source);

CREATE TABLE IF NOT EXISTS processed_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_signal_id   UUID NOT NULL REFERENCES raw_signals(id) ON DELETE CASCADE,
    dedup_key       TEXT NOT NULL UNIQUE,
    is_relevant     BOOLEAN NOT NULL DEFAULT false,
    matched_rules   JSONB NOT NULL DEFAULT '[]',
    summary         TEXT,
    products_mentioned TEXT[] DEFAULT '{}',
    intensity       SMALLINT CHECK (intensity BETWEEN 1 AND 5),
    confidence      NUMERIC(3,2),
    keywords_matched TEXT[] DEFAULT '{}',
    language        TEXT,
    rank_score      NUMERIC(8,4),
    linked_group_id UUID,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_processed_keywords    ON processed_signals USING GIN (keywords_matched);
CREATE INDEX IF NOT EXISTS idx_processed_products    ON processed_signals USING GIN (products_mentioned);
CREATE INDEX IF NOT EXISTS idx_processed_at          ON processed_signals (processed_at);
CREATE INDEX IF NOT EXISTS idx_processed_rank        ON processed_signals (rank_score DESC);
CREATE INDEX IF NOT EXISTS idx_processed_relevant    ON processed_signals (is_relevant);

CREATE TABLE IF NOT EXISTS embedding_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dedup_key       TEXT NOT NULL UNIQUE REFERENCES processed_signals(dedup_key) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','failed')),
    attempts        SMALLINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_attempt_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_embedding_queue_status ON embedding_queue (status, created_at) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS collection_cursors (
    collector_name  TEXT NOT NULL,
    target_key      TEXT NOT NULL,
    last_collected_at TIMESTAMPTZ NOT NULL,
    last_cursor     TEXT,
    PRIMARY KEY (collector_name, target_key)
);

CREATE TABLE IF NOT EXISTS keyword_profiles (
    canonical_name      TEXT PRIMARY KEY,
    raw                 TEXT NOT NULL,
    keyword_type        TEXT NOT NULL,
    description         TEXT,
    profile_data        JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_collected_at   TIMESTAMPTZ
);
-- Migration: add last_collected_at if it doesn't exist yet
DO $$ BEGIN
    ALTER TABLE keyword_profiles ADD COLUMN IF NOT EXISTS last_collected_at TIMESTAMPTZ;
END $$;

CREATE TABLE IF NOT EXISTS keyword_collection_plans (
    canonical_name  TEXT NOT NULL,
    collector_name  TEXT NOT NULL,
    plan_data       JSONB NOT NULL,
    approved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (canonical_name, collector_name)
);

CREATE TABLE IF NOT EXISTS change_report_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    keyword         TEXT NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_start    TIMESTAMPTZ NOT NULL,
    period_end      TIMESTAMPTZ NOT NULL,
    report_text     TEXT,
    signal_count    INT DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_report_snapshots_keyword ON change_report_snapshots (keyword, generated_at DESC);

CREATE TABLE IF NOT EXISTS llm_task_queue (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type    TEXT        NOT NULL,
    priority     SMALLINT    NOT NULL DEFAULT 50,
    payload      JSONB       NOT NULL DEFAULT '{}',
    status       TEXT        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'running', 'failed')),
    retry_count  SMALLINT    NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_llm_task_queue_next
    ON llm_task_queue (priority ASC, created_at ASC)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS llm_usage_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider        TEXT NOT NULL,
    operation       TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INT DEFAULT 0,
    output_tokens   INT DEFAULT 0,
    cost_usd        NUMERIC(10, 6) DEFAULT 0,
    logged_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_logged_at ON llm_usage_log (logged_at);
