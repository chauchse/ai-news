-- Additive and rerunnable. Daily balance votes never become article labels.
CREATE TABLE IF NOT EXISTS feedback_newsletter_returns (
    id TEXT PRIMARY KEY, submission_id TEXT NOT NULL UNIQUE,
    presentation_id TEXT NOT NULL, briefing_date DATE NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN
        ('newsletter_balanced', 'newsletter_too_cyber', 'newsletter_too_ai')),
    submitted_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_newsletter_returns_day ON feedback_newsletter_returns(briefing_date);
CREATE OR REPLACE VIEW feedback_effective_newsletter_returns AS
SELECT DISTINCT ON (presentation_id) * FROM feedback_newsletter_returns
ORDER BY presentation_id, submitted_at DESC, id DESC;
