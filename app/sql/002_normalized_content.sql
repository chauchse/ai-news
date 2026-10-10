ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS normalized_content TEXT,
    ADD COLUMN IF NOT EXISTS normalized_summary TEXT;
