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

-- Deductions are local reports, never fabricated signed submissions.
-- Preserve the existing view columns and give every explicit vote priority.
CREATE OR REPLACE VIEW feedback_effective_returns AS
SELECT explicit.* FROM (
    SELECT DISTINCT ON (presentation_id) * FROM feedback_returns
    ORDER BY presentation_id, submitted_at DESC, id DESC
) AS explicit
UNION ALL
SELECT inferred.* FROM (
SELECT DISTINCT ON (item->>'presentation_id')
    item->>'id' AS id,
    item->>'submission_id' AS submission_id,
    item->>'presentation_id' AS presentation_id,
    'useful'::text AS reason,
    run.created_at AS submitted_at,
    run.created_at AS received_at,
    run.created_at AS imported_at,
    item AS payload
FROM feedback_runs AS run
CROSS JOIN LATERAL jsonb_array_elements(run.report->'items') AS item
WHERE run.kind='implicit_useful'
  AND NOT EXISTS (
      SELECT 1 FROM feedback_returns AS vote
      WHERE vote.presentation_id=item->>'presentation_id'
  )
ORDER BY item->>'presentation_id', run.created_at DESC, run.id DESC
) AS inferred;
