CREATE OR REPLACE VIEW unique_articles AS
SELECT DISTINCT ON (guid)
    *
FROM articles
ORDER BY
    guid,
    relevance_score DESC NULLS LAST,
    first_seen_at ASC,
    id ASC;
