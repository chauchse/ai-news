"""Optional isolated PostgreSQL migration and import integration."""
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest

import psycopg
from feedback.importer import Store
from mailer.feedback_links import newsletter_identity

PG_BIN = os.getenv('AI_NEWS_TEST_PG_BIN')
PG_SHARE = os.getenv('AI_NEWS_TEST_PG_SHARE')


@unittest.skipUnless(PG_BIN and PG_SHARE, 'Set AI_NEWS_TEST_PG_BIN and AI_NEWS_TEST_PG_SHARE')
class NewsletterPostgresTests(unittest.TestCase):
    def test_migration_replay_corrections_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            subprocess.run([str(Path(PG_BIN) / 'initdb'), '-D', str(data), '-L', PG_SHARE,
                            '-U', 'newsletter_test', '-A', 'trust', '--no-locale', '-E', 'UTF8'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            control = [str(Path(PG_BIN) / 'pg_ctl'), '-D', str(data)]
            subprocess.run(control + ['-l', str(root / 'pg.log'), '-o', f"-k {root} -h '' -p {port}", '-w', 'start'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                with psycopg.connect(host=str(root), port=port, dbname='postgres', user='newsletter_test') as conn:
                    for _ in range(2):
                        for migration in ('005_feedback.sql', '006_newsletter_feedback.sql'):
                            conn.execute((Path('app/sql') / migration).read_text())
                        conn.commit()
                    def row(id_, reason='newsletter_balanced', time='2026-10-13T00:00:00Z'):
                        return dict(schema_version=1, id=id_ * 64, submission_id=id_ * 64,
                                    presentation_id=newsletter_identity('2026-10-13'), briefing_date='2026-10-13',
                                    reason=reason, submitted_at=time, received_at=time)
                    first = row('a')
                    correction = row('b', 'newsletter_too_cyber', '2026-10-13T00:00:01Z')
                    article = row('c', 'useful')
                    del article['briefing_date']
                    article['presentation_id'] = 'c' * 64
                    store = Store(conn)
                    self.assertEqual(store.save([first, correction, article])[1], 3)
                    conn.commit()
                    self.assertEqual(store.save([first, correction, article])[1], 0)
                    conn.commit()
                    with conn.cursor() as cur:
                        cur.execute('SELECT reason FROM feedback_effective_newsletter_returns')
                        self.assertEqual(cur.fetchall(), [('newsletter_too_cyber',)])
                        cur.execute('SELECT reason FROM feedback_effective_returns')
                        self.assertEqual(cur.fetchall(), [('useful',)])
                    conn.commit()
                    bad = dict(row('e'), briefing_date='invalid-date')
                    with self.assertRaises(psycopg.Error):
                        store.save([row('d'), bad])
                    self.assertEqual(conn.execute('SELECT count(*) FROM feedback_newsletter_returns').fetchone()[0], 2)
            finally:
                subprocess.run(control + ['-m', 'fast', '-w', 'stop'], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
