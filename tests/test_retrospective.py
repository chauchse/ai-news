"""Date boundaries without loading inference models or accessing the network."""
import ast
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock
from psycopg import sql
from zoneinfo import ZoneInfo

from podcast.publish import append_episode
from mailer.send import build_message

_tree = ast.parse(Path('embeddings/briefing.py').read_text())
_ns = dict(datetime=datetime, timedelta=timedelta, ZoneInfo=ZoneInfo)
exec(compile(ast.Module(body=[n for n in _tree.body if isinstance(n, ast.FunctionDef)
                             and n.name == 'retrospective_bounds'], type_ignores=[]), '<dates>', 'exec'), _ns)
bounds = _ns['retrospective_bounds']


class RetrospectiveTests(unittest.TestCase):
    def test_default_and_invalid_dates(self):
        self.assertIsNone(bounds(None))
        self.assertIsNone(bounds(''))
        for value in ('2025-2-01', '2025-02-30', '2999-01-01'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bounds(value)

    def test_sql_uses_publication_date_and_half_open_window(self):
        ns = dict(_ns, os=os, sql=sql, DATABASE_URL='unused',
                  load_rules=Mock(return_value={}), psycopg=MagicMock())
        cursor = ns['psycopg'].connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[('url',)], []]
        node = next(n for n in _tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fetch_articles')
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<fetch>', 'exec'), ns)
        with patch.dict(os.environ, {'BRIEFING_DATE': '2025-03-30'}):
            self.assertEqual(ns['fetch_articles']({'briefing': {'lookback_hours': 24}}), [])
        query, params = cursor.execute.call_args.args
        self.assertIn('published_at >= %s AND published_at < %s', query.as_string())
        self.assertNotIn('first_seen_at', query.as_string())
        self.assertEqual(params[:2], bounds('2025-03-30'))
        cursor.fetchall.side_effect = [[('url',)], []]
        with patch.dict(os.environ, {'BRIEFING_DATE': ''}):
            ns['fetch_articles']({'briefing': {'lookback_hours': 24}})
        query, params = cursor.execute.call_args.args
        self.assertIn('NOW()', query.as_string())
        self.assertEqual(params[0], 24)

    def test_calendar_days_include_dst(self):
        for day, hours in [('2025-03-30', 23), ('2025-10-26', 25), ('2025-10-10', 24)]:
            start, end = bounds(day)
            self.assertEqual(start.date().isoformat(), day)
            self.assertEqual(start.hour, 0)
            self.assertEqual(end.hour, 0)
            self.assertEqual((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds(), hours * 3600)

    @patch('podcast.publish.get_audio_duration', return_value=None)
    def test_backfill_preserves_newer_episode_and_retry_date(self, duration):
        now = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo('Europe/Paris'))
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'test.mp3'
            audio.write_bytes(b'audio')
            state, current = append_episode([], audio, [], now, 7)
            state, older = append_episode(state, audio, [], now, 7, edition_date=now.date() - timedelta(days=2))
            self.assertEqual(len(state), 2)
            self.assertIn(current, state)
            self.assertEqual(older['date'], '2026-10-11')
            self.assertEqual(older['published_at'], now.isoformat())
            state, retry = append_episode(state, audio, [], now + timedelta(hours=1), 7,
                                          edition_date=now.date() - timedelta(days=2))
            self.assertEqual(len(state), 2)
            self.assertEqual(retry['published_at'], now.isoformat())
            with self.assertRaises(ValueError):
                append_episode(state, audio, [], now, 7, edition_date=now.date() - timedelta(days=7))

    def test_historical_mail_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'briefing.md'
            path.write_text('# Briefing\n\nContent')
            cfg = dict(input=str(path), edition_date='2025-10-10', subject='Briefing {date}',
                       sender='sender@example.org', recipient='recipient@example.org')
            with patch.dict(os.environ, {'SENDER': '', 'RECIPIENT': ''}):
                self.assertEqual(build_message(cfg)['Subject'], 'Briefing 2025-10-10')


class RetrospectivePipelineTests(unittest.TestCase):
    def test_prepare_resume_and_explicit_publication(self):
        import json
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path('run-daily.sh').read_text().replace('/var/log/ai-news.log', str(root / 'pipeline.log')).replace('/run/ai-news.lock', str(root / 'pipeline.lock'))
            script = root / 'run-daily.sh'
            script.write_text(source)
            docker = root / 'docker'
            docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
root = Path(os.environ['MOCK_ROOT'])
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps(a) + '\\n')
if 'project_config' in a and 'engine' in a:
    print('kokoro')
if 'embeddings.briefing' in a:
    value = next(x for x in a if x.startswith('BRIEFING_DAILY_DIR='))
    daily = root / value.split('=', 1)[1].removeprefix('/')
    for lang in ('en', 'fr'):
        p = daily / lang / 'briefing.md'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('frozen markdown')
if '--output' in a:
    p = root / a[a.index('--output') + 1].removeprefix('/')
    day = a[a.index('--date') + 1]
    p.with_name(day + '-' + p.name).write_bytes(b'audio')
if '--preview' in a:
    p = root / a[a.index('--preview') + 1].removeprefix('/')
    p.write_text('preview')
''')
            docker.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], MOCK_ROOT=str(root))
            day = '2025-10-10'
            def run(*flags):
                result = subprocess.run(['bash', str(script), '--date', day, *flags], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run()
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
            self.assertFalse(any('app.scorer' in c for c in calls))
            self.assertFalse(any('collector' in c and '--entrypoint' not in c for c in calls))
            self.assertTrue(all('--dry-run' in c for c in calls if 'mailer' in c and '--entrypoint' not in c))
            self.assertFalse(any('podcast' in c and '--entrypoint' not in c for c in calls))
            edition = root / 'output/daily' / day / 'fr'
            self.assertTrue((edition / 'audio.done').exists())
            self.assertFalse((edition / 'mail.done').exists())
            frozen = (edition / 'briefing.md').read_bytes()
            run('--publish')
            self.assertTrue((edition / 'podcast.done').exists())
            self.assertFalse((edition / 'mail.done').exists())
            run('--publish', '--send-mail')
            self.assertTrue((edition / 'mail.done').exists())
            self.assertEqual((edition / 'briefing.md').read_bytes(), frozen)
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
            self.assertEqual(sum('embeddings.briefing' in c for c in calls), 1)
            self.assertEqual(sum('podcast' in c and '--entrypoint' not in c and '--dry-run' not in c for c in calls), 2)
