import os
import psycopg

from app.normalizer import normalize_text


def main():
    database_url = os.environ["DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, content, summary
                FROM articles
                WHERE normalized_content IS NULL
                   OR normalized_summary IS NULL
                ORDER BY id
            """)

            articles = cur.fetchall()
            print(f"Articles à normaliser : {len(articles)}")

            for article_id, content, summary in articles:
                cur.execute("""
                    UPDATE articles
                    SET normalized_content = %s,
                        normalized_summary = %s
                    WHERE id = %s
                """, (
                    normalize_text(content),
                    normalize_text(summary),
                    article_id
                ))

        conn.commit()

    print(f"Normalisation terminée : {len(articles)} articles")


if __name__ == "__main__":
    main()
