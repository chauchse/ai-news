import hashlib
import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from app.normalizer import normalize_text
import urllib.request

import feedparser
import psycopg
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("ai-news")

DATABASE_URL = os.environ["DATABASE_URL"]
CONFIG_PATH = os.getenv("SOURCES_FILE", "/app/config/sources.yaml")


def parse_date(entry):
    value = entry.get("published") or entry.get("updated")

    if value:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError, OverflowError):
            pass

    for field in ("published_parsed", "updated_parsed"):
        value = entry.get(field)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass

    return None


def article_content(entry):
    content = entry.get("content") or []
    if content and content[0].get("value"):
        return content[0]["value"]
    return entry.get("summary", "")


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    with psycopg.connect(DATABASE_URL) as conn:
        for source in config.get("sources", []):
            if not source.get("enabled", True):
                continue

            name = source["name"]
            feed_url = source["url"]
            run_id = None

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO sources (name, feed_url)
                        VALUES (%s, %s)
                        ON CONFLICT (feed_url)
                        DO UPDATE SET name = EXCLUDED.name
                        RETURNING id
                        """,
                        (name, feed_url),
                    )
                    source_id = cur.fetchone()[0]

                    cur.execute(
                        """
                        INSERT INTO collection_runs (source_id)
                        VALUES (%s)
                        RETURNING id
                        """,
                        (source_id,),
                    )
                    run_id = cur.fetchone()[0]

                # Request the feed with a timeout; do not trust feed contents.
                request = urllib.request.Request(
                    feed_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (X11; Linux x86_64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": (
                            "application/rss+xml, application/atom+xml, "
                            "application/xml, text/xml;q=0.9, */*;q=0.8"
                        ),
                        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
                    },
                )

                with urllib.request.urlopen(request, timeout=15) as response:
                    feed_data = response.read(10 * 1024 * 1024 + 1)

                if len(feed_data) > 10 * 1024 * 1024:
                    raise ValueError("Flux RSS trop volumineux (> 10 Mo)")

                feed = feedparser.parse(feed_data)

                if feed.bozo and not feed.entries:
                    raise ValueError(
                        f"Flux RSS invalide : {feed.get('bozo_exception')}"
                    )

                found = 0
                inserted = 0

                with conn.cursor() as cur:
                    for entry in feed.entries:
                        url = entry.get("link", "").strip()
                        title = entry.get("title", "").strip()

                        if not url or not title:
                            continue

                        found += 1

                        # Some feeds omit a GUID. Use the URL as a fallback.
                        guid = (entry.get("id") or url).strip()
                        summary = entry.get("summary", "")
                        content = article_content(entry)
                        published_at = parse_date(entry)
                        author = entry.get("author")

                        text_to_hash = (
                            title + "\n" + summary + "\n" + content
                        )
                        content_hash = hashlib.sha256(
                            text_to_hash.encode("utf-8")
                        ).hexdigest()

                        cur.execute(
                            """
                            INSERT INTO articles (
                                source_id, guid, url, title, author,
                                summary, content, published_at, content_hash
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (source_id, guid) DO NOTHING
                            RETURNING id
                            """,
                            (
                                source_id, guid, url, title, author,
                                summary, content, published_at, content_hash,
                            ),
                        )
                        if cur.fetchone():
                            inserted += 1

                    cur.execute(
                        "UPDATE sources SET last_fetched_at = NOW() WHERE id = %s",
                        (source_id,),
                    )
                    cur.execute(
                        """
                        UPDATE collection_runs
                        SET finished_at = NOW(),
                            status = %s,
                            articles_found = %s,
                            articles_inserted = %s
                        WHERE id = %s
                        """,
                        (
                            "partial" if feed.bozo else "success",
                            found,
                            inserted,
                            run_id,
                        ),
                    )


                conn.commit()
                log.info(
                    "%s : %d articles trouvés, %d nouveaux",
                    name, found, inserted,
                )

            except Exception as exc:
                conn.rollback()
                log.exception("Échec de la collecte de %s", name)

                # Record the failure separately, without hiding the original error.
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO sources (name, feed_url)
                            VALUES (%s, %s)
                            ON CONFLICT (feed_url)
                            DO UPDATE SET name = EXCLUDED.name
                            RETURNING id
                            """,
                            (name, feed_url),
                        )
                        source_id = cur.fetchone()[0]

                        cur.execute(
                            """
                            INSERT INTO collection_runs
                                (source_id, finished_at, status, error_message)
                            VALUES (%s, NOW(), 'failed', %s)
                            """,
                            (source_id, str(exc)[:4000]),
                        )
                    conn.commit()

                except Exception:
                    conn.rollback()
                    log.exception("Impossible d'enregistrer l'échec de collecte")
        normalize_pending_articles(conn)

def normalize_pending_articles(conn):
    """Normalise les articles qui ne le sont pas encore."""

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, content, summary
            FROM articles
            WHERE normalized_content IS NULL
               OR normalized_summary IS NULL
        """)

        articles = cur.fetchall()

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
    print(f"Articles normalisés : {len(articles)}")

if __name__ == "__main__":
    main()
