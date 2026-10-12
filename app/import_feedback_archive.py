"""Resolve archived test presentations without inventing historical ML features.

Usage: python -m app.import_feedback_archive --archive-id 3
Requires DATABASE_URL and the existing feedback schema. No network or mail.
"""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import psycopg
from psycopg.types.json import Jsonb


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode()).hexdigest()


def source_key(url):
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return 'url:' + urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(query), ''))


def presentations(archive):
    """Validate bilingual identity and retain exactly the archived edition text."""
    if archive.get('schema_version') != 1 or not archive.get('test_only'):
        raise ValueError('Unsupported archive')
    manifest = archive['manifest']
    day = date.fromisoformat(manifest['date']).isoformat()
    editions = manifest['editions']
    if set(editions) != {'en', 'fr'} or set(archive['briefings']) != {'en', 'fr'}:
        raise ValueError('Both archived editions are required')
    result = {}
    ids = {}
    for language, rows in editions.items():
        markdown = archive['briefings'][language]
        blocks = re.split(r'(?=^### )', markdown, flags=re.M)
        content = {}
        for block in blocks:
            matches = re.findall(r'<!-- feedback:([a-f0-9]{64}) -->', block)
            if matches:
                if len(matches) != 1 or matches[0] in content:
                    raise ValueError('Ambiguous presentation marker')
                content[matches[0]] = block.split('<!-- feedback:')[0].rstrip()
        ids[language] = set()
        for row in rows:
            pid, event = row['presentation_id'], row['event_id']
            if not all(isinstance(i, str) and re.fullmatch(r'[a-f0-9]{64}', i) for i in (pid, event)):
                raise ValueError('Invalid identity')
            urls = row['sources']
            if not isinstance(urls, list) or not urls or any(
                not isinstance(u, str) or urlsplit(u).scheme not in ('http', 'https')
                or not urlsplit(u).hostname for u in urls):
                raise ValueError('Invalid source URLs')
            urls = sorted(set(urls))
            if event != digest({'scope': 'legacy-feedback-test-v1', 'sources': urls}):
                raise ValueError('Event identity mismatch')
            if pid != digest({'event': event, 'briefing_date': day, 'scope': 'legacy-feedback-test-v1'}):
                raise ValueError('Presentation identity mismatch')
            if pid in ids[language] or pid not in content or content[pid].splitlines()[0] != row['title']:
                raise ValueError('Missing, duplicate or mismatched edition content')
            ids[language].add(pid)
            current = result.setdefault(pid, dict(presentation_id=pid, event_id=event,
                                                  sources=urls, editions={}))
            if current['event_id'] != event or current['sources'] != urls:
                raise ValueError('Bilingual identity mismatch')
            current['editions'][language] = dict(title=row['title'], markdown=content[pid])
        if set(content) != ids[language]:
            raise ValueError('Unmapped archived presentation')
    if ids['en'] != ids['fr']:
        raise ValueError('Bilingual editions differ')
    return day, result


def import_archive(conn, archive_id):
    stats = dict(events_inserted=0, briefings_inserted=0, presentations_inserted=0,
                 article_links_inserted=0, reports_inserted=0, source_urls_unmatched=0)
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(74110322)')
        row = conn.execute("SELECT report FROM feedback_runs WHERE id=%s AND kind='legacy_test_archive'",
                           (archive_id,)).fetchone()
        if row is None:
            raise ValueError('Archive not found')
        archive = row[0]
        day, items = presentations(archive)
        before_votes = conn.execute('SELECT count(*) FROM feedback_returns').fetchone()[0]
        briefing_id = digest({'scope': 'legacy-feedback-test-v1', 'day': day})
        cfg = dict(origin='legacy_test_archive', archive_run_id=archive_id, test_only=True,
                   retrospective=True, original_config_available=False)
        stats['briefings_inserted'] = conn.execute('''INSERT INTO feedback_briefings(id,day,config)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''', (briefing_id, day, Jsonb(cfg))).rowcount
        existing = conn.execute('SELECT day,config FROM feedback_briefings WHERE id=%s', (briefing_id,)).fetchone()
        if existing[0].isoformat() != day or existing[1] != cfg:
            raise ValueError('Conflicting briefing; nothing overwritten')
        articles = defaultdict(list)
        for aid, url in conn.execute('SELECT id,url FROM articles'):
            articles[source_key(url)].append(aid)
        for pid, item in items.items():
            event_id = item['event_id']
            stats['events_inserted'] += conn.execute('''INSERT INTO feedback_events(id)
                VALUES (%s) ON CONFLICT DO NOTHING''', (event_id,)).rowcount
            snapshot = dict(origin='legacy_test_archive', archive_run_id=archive_id,
                            test_only=True, retrospective=True, source_urls=item['sources'],
                            published_editions=item['editions'], decision_features_available=False)
            stats['presentations_inserted'] += conn.execute('''INSERT INTO feedback_candidates
                (presentation_id,briefing_id,event_id,snapshot,assessment,evaluated,eligible,
                 selected,synthesized,editions)
                VALUES (%s,%s,%s,%s,'{}',false,true,true,true,%s) ON CONFLICT DO NOTHING''',
                (pid, briefing_id, event_id, Jsonb(snapshot), Jsonb({lang: True for lang in item['editions']}))).rowcount
            existing = conn.execute('''SELECT briefing_id,event_id,snapshot FROM feedback_candidates
                WHERE presentation_id=%s''', (pid,)).fetchone()
            if existing is None or existing != (briefing_id, event_id, snapshot):
                raise ValueError('Conflicting presentation; nothing overwritten')
            for url in item['sources']:
                key = source_key(url)
                matches = articles.get(key, [])
                if not matches: stats['source_urls_unmatched'] += 1
                for aid in matches:
                    stats['article_links_inserted'] += conn.execute('''INSERT INTO feedback_event_articles
                        (event_id,article_id,source_key) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''',
                        (event_id, aid, key)).rowcount
        for filename, data in archive.get('analysis_reports', {}).items():
            exists = conn.execute("""SELECT 1 FROM feedback_runs WHERE kind='legacy_test_report'
                AND report->>'archive_run_id'=%s AND report->>'filename'=%s""", (str(archive_id), filename)).fetchone()
            if not exists:
                conn.execute("INSERT INTO feedback_runs(kind,report) VALUES ('legacy_test_report',%s)",
                             (Jsonb(dict(archive_run_id=archive_id, filename=filename, data=data, test_only=True)),))
                stats['reports_inserted'] += 1
        stats['votes_resolved'] = conn.execute('''SELECT count(*) FROM feedback_returns
            WHERE presentation_id = ANY(%s)''', (list(items),)).fetchone()[0]
        stats['unresolved_votes_remaining'] = conn.execute('''SELECT count(*) FROM feedback_returns r
            LEFT JOIN feedback_candidates c USING(presentation_id) WHERE c.presentation_id IS NULL''').fetchone()[0]
        if conn.execute('SELECT count(*) FROM feedback_returns').fetchone()[0] != before_votes:
            raise ValueError('Vote count unexpectedly changed')
        conn.execute("INSERT INTO feedback_runs(kind,report) VALUES ('legacy_archive_import',%s)",
                     (Jsonb(dict(stats, archive_run_id=archive_id)),))
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-id', type=int, required=True)
    args = parser.parse_args()
    try:
        with psycopg.connect(os.environ['DATABASE_URL']) as conn:
            stats = import_archive(conn, args.archive_id)
        print(json.dumps(stats, sort_keys=True))
    except Exception as exc:
        parser.exit(1, 'Archive import failed (' + type(exc).__name__ + '); transaction rolled back.\n')


if __name__ == '__main__':
    main()
