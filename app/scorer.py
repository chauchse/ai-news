import os
import re
from datetime import datetime, timezone

import psycopg
import yaml


CONFIG_PATH = os.getenv(
    "SCORING_FILE", "/app/config/scoring.yaml"
)


def score_article(title, content, published_at, topics):
    title = (title or "").lower()
    content = (content or "").lower()
    text = f"{title} {content}"

    # 1. Pertinence thématique : 0 à 60
    topic_score = 0

    for topic in topics:
        keywords = topic.get("keywords", [])

        matches = sum(
            1 for keyword in keywords
            if keyword.lower() in text
        )

        if matches:
            candidate = min(
                topic["weight"],
                matches * 15
            )
            topic_score = max(topic_score, candidate)

    topic_score = min(topic_score, 60)

    # 2. Fraîcheur : 0 à 25
    freshness_score = 0

    if published_at:
        now = datetime.now(timezone.utc)
        age_hours = max(
            0,
            (now - published_at).total_seconds() / 3600
        )

        if age_hours <= 24:
            freshness_score = 25
        elif age_hours <= 48:
            freshness_score = 20
        elif age_hours <= 72:
            freshness_score = 15
        elif age_hours <= 168:
            freshness_score = 5

    # 3. Richesse du contenu : 0 à 15
    length = len(content)

    if length >= 2000:
        quality_score = 15
    elif length >= 1000:
        quality_score = 10
    elif length >= 500:
        quality_score = 5
    else:
        quality_score = 0

    return min(
        100,
        topic_score + freshness_score + quality_score
    )


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    topics = config.get("topics", [])

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, normalized_content, published_at
                FROM articles
                WHERE relevance_score IS NULL
                OR scored_at < NOW() - INTERVAL '12 hours'
            """)

            articles = cur.fetchall()

            for article_id, title, content, published_at in articles:
                score = score_article(
                    title, content, published_at, topics
                )

                cur.execute("""
                    UPDATE articles
                    SET relevance_score = %s,
                        scored_at = NOW()
                    WHERE id = %s
                """, (score, article_id))

        conn.commit()

    print(f"Articles évalués : {len(articles)}")


if __name__ == "__main__":
    main()
