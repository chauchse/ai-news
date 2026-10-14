-- Preserve the existing feedback schema and archived test data.
-- Additive, rerunnable; does not enable mail buttons or ML in the current pipeline.
CREATE TABLE IF NOT EXISTS feedback_events (
    id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS feedback_aliases (
    alias TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES feedback_events(id)
);
-- Deliberately no FK to articles: collection purges must not erase learning history.
CREATE TABLE IF NOT EXISTS feedback_event_articles (
    event_id TEXT NOT NULL REFERENCES feedback_events(id), article_id BIGINT NOT NULL,
    source_key TEXT NOT NULL, PRIMARY KEY(event_id, article_id)
);
CREATE TABLE IF NOT EXISTS feedback_briefings (
    id TEXT PRIMARY KEY, day DATE NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    config JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback_candidates (
    presentation_id TEXT PRIMARY KEY,
    briefing_id TEXT NOT NULL REFERENCES feedback_briefings(id),
    event_id TEXT NOT NULL REFERENCES feedback_events(id),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    snapshot JSONB NOT NULL, assessment JSONB NOT NULL,
    evaluated BOOLEAN NOT NULL DEFAULT FALSE, eligible BOOLEAN NOT NULL DEFAULT FALSE,
    selected BOOLEAN NOT NULL DEFAULT FALSE, synthesized BOOLEAN NOT NULL DEFAULT FALSE,
    errors JSONB NOT NULL DEFAULT '[]', editions JSONB NOT NULL DEFAULT '{}',
    UNIQUE(briefing_id, event_id)
);
-- Unknown presentations must remain importable and durable, hence no presentation FK.
CREATE TABLE IF NOT EXISTS feedback_returns (
    id TEXT PRIMARY KEY, submission_id TEXT NOT NULL UNIQUE,
    presentation_id TEXT NOT NULL, reason TEXT NOT NULL CHECK (reason IN
      ('useful','off_topic','not_applicable','shallow_or_promotional','not_new','already_covered')),
    submitted_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_returns_presentation ON feedback_returns(presentation_id);
CREATE TABLE IF NOT EXISTS feedback_cache (
    key TEXT PRIMARY KEY, value JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS feedback_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), kind TEXT NOT NULL, report JSONB NOT NULL
);
CREATE OR REPLACE VIEW feedback_effective_returns AS
SELECT DISTINCT ON (presentation_id) * FROM feedback_returns
ORDER BY presentation_id, submitted_at DESC, id DESC;
