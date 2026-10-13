"""Persistent identity, candidates and cache. Never deletes source or learning data."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from feedback.common import digest


def source_key(article):
    url = article.get('url')
    if url:
        p = urlsplit(url)
        query = [(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]
        key = urlunsplit((p.scheme.lower(),p.netloc.lower().removeprefix('www.'),p.path.rstrip('/') or '/',urlencode(query),''))
        identifiers = sorted({v.upper() for v in re.findall(r'\b(?:CVE-\d{4}-\d{4,}|ZDI-CAN-\d+)\b',
            article.get('title','')+' '+article.get('summary',''),re.I)})
        if article.get('vulnerability',{}).get('candidate') and identifiers:
            key += '#advisory=' + digest(identifiers)
        return key
    return 'article:' + str(article['id'])


def serialized(value):
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)))


class Repository:
    def __init__(self, conn, day, cfg):
        from psycopg.types.json import Jsonb
        self.conn, self.Jsonb = conn, Jsonb
        self.briefing_id = digest(['briefing-v1', day])
        self.cfg = cfg
        with conn.transaction():
            conn.execute('INSERT INTO feedback_briefings(id,day,config) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                         (self.briefing_id, day, Jsonb(serialized(cfg))))

    def register(self, groups):
        mapping = {}
        with self.conn.transaction():
            # Serializes alias ownership updates, never an inference or network call.
            self.conn.execute('SELECT pg_advisory_xact_lock(74110322)')
            for group in groups:
                keys = sorted({source_key(a) for a in group})
                aliases = ['source:' + digest(key) for key in keys]
                rows = self.conn.execute('SELECT DISTINCT event_id FROM feedback_aliases WHERE alias=ANY(%s) ORDER BY event_id', (aliases,)).fetchall()
                event_id = rows[0][0] if rows else digest(['event-v1', keys])
                self.conn.execute('INSERT INTO feedback_events(id) VALUES (%s) ON CONFLICT DO NOTHING', (event_id,))
                for alias in aliases:
                    self.conn.execute('INSERT INTO feedback_aliases(alias,event_id) VALUES (%s,%s) ON CONFLICT DO NOTHING', (alias,event_id))
                presentation = digest(['presentation-v1', self.briefing_id, event_id])
                snapshot = dict(schema_version=1, articles=serialized(group), identity_alias_events=[r[0] for r in rows])
                # Existing snapshots remain immutable on same-day restarts.
                self.conn.execute('''INSERT INTO feedback_candidates(presentation_id,briefing_id,event_id,snapshot,assessment)
                    VALUES (%s,%s,%s,%s,'{"reason":"not_in_evaluation_pool"}') ON CONFLICT DO NOTHING''',
                    (presentation,self.briefing_id,event_id,self.Jsonb(snapshot)))
                for article in group:
                    self.conn.execute('INSERT INTO feedback_event_articles(event_id,article_id,source_key) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                        (event_id,article['id'],source_key(article)))
                    article['event_id'], article['presentation_id'] = event_id, presentation
                mapping[id(group)] = presentation
        return mapping

    def excluded_history(self, before, after):
        kept = {id(g) for g in after}
        with self.conn.transaction():
            for group in before:
                if id(group) not in kept:
                    self.conn.execute("UPDATE feedback_candidates SET assessment=%s WHERE presentation_id=%s",
                        (self.Jsonb({'reason':'already_covered_without_material_update'}), group[0]['presentation_id']))

    def assess(self, assessed, selected, eligible):
        with self.conn.transaction():
            for item in assessed:
                pid = item['group'][0]['presentation_id']
                self.conn.execute('''UPDATE feedback_candidates SET assessment=%s,evaluated=TRUE,eligible=%s,selected=%s,observed_at=%s
                    WHERE presentation_id=%s''', (self.Jsonb(serialized({k:v for k,v in item.items() if k != 'group'})),
                        item in eligible, item in selected,
                        item.get('personalization',{}).get('decision_time') or datetime.now(timezone.utc),pid))

    def stage(self, group, stage, language=None, error=None):
        pid = group[0]['presentation_id']
        with self.conn.transaction():
            if error:
                self.conn.execute('UPDATE feedback_candidates SET errors=errors || %s WHERE presentation_id=%s',
                    (self.Jsonb([{'stage':stage,'language':language,'type':type(error).__name__}]),pid))
            elif stage == 'synthesized':
                self.conn.execute('UPDATE feedback_candidates SET synthesized=TRUE WHERE presentation_id=%s',(pid,))
            elif stage == 'edition':
                self.conn.execute('UPDATE feedback_candidates SET editions=editions || %s WHERE presentation_id=%s',
                    (self.Jsonb({language:True}),pid))

    def cache(self, key, value=None):
        if value is None:
            row = self.conn.execute('SELECT value FROM feedback_cache WHERE key=%s',(key,)).fetchone()
            self.conn.commit()
            return row[0] if row else None
        with self.conn.transaction():
            self.conn.execute('INSERT INTO feedback_cache(key,value) VALUES (%s,%s) ON CONFLICT DO NOTHING', (key,self.Jsonb(value)))

    def annotated(self, before):
        # Vote availability is imported_at, not merely the public receipt time.
        rows = self.conn.execute('''WITH effective AS (
            SELECT DISTINCT ON (presentation_id) * FROM feedback_returns
            WHERE imported_at < %s ORDER BY presentation_id,submitted_at DESC,id DESC)
            SELECT c.event_id,c.presentation_id,c.snapshot,c.assessment,c.observed_at,
                   r.reason,r.imported_at,r.submitted_at
            FROM feedback_candidates c JOIN effective r USING(presentation_id)
            WHERE c.observed_at < %s AND c.synthesized AND c.editions <> '{}'
            ORDER BY c.observed_at,c.presentation_id''',(before,before)).fetchall()
        self.conn.commit()
        return [dict(zip(('event_id','presentation_id','snapshot','assessment','observed_at','reason','available_at','submitted_at'),row)) for row in rows]

    def report(self, kind, value):
        with self.conn.transaction():
            self.conn.execute('INSERT INTO feedback_runs(kind,report) VALUES (%s,%s)', (kind,self.Jsonb(serialized(value))))
