"""Optional real PHP integration against a loopback-only temporary receiver."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, parse_qs, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from mailer.feedback_links import links, newsletter_identity, validate_return

PHP = os.getenv('AI_NEWS_TEST_PHP') or shutil.which('php')


@unittest.skipUnless(PHP, 'Set AI_NEWS_TEST_PHP to a PHP >= 8.1 binary')
class NewsletterPHPTests(unittest.TestCase):
    def test_confirm_correct_pull_ack_and_legacy_direct_vote(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public, pending = root / 'public', root / 'pending'
            public.mkdir(); pending.mkdir()
            shutil.copy('web/public/feedback.php', public / 'feedback.php')
            config = root / 'config.php'
            template = Path('web/private.example/config.php').read_text().replace(
                'REPLACE_WITH_AT_LEAST_32_RANDOM_BYTES', 'x' * 32).replace(
                'REPLACE_WITH_A_DIFFERENT_RANDOM_SECRET', 'y' * 32)
            config.write_text(template)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            base = f'http://127.0.0.1:{port}/feedback.php'
            proc = subprocess.Popen([PHP, '-n', '-S', f'127.0.0.1:{port}', '-t', str(public)],
                                    env=dict(os.environ, FEEDBACK_PRIVATE_CONFIG=str(config)),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def request(suffix='', data=None, auth=False):
                headers = {'Authorization': 'Bearer ' + 'y' * 32} if auth else {}
                if data is not None:
                    headers['Content-Type'] = 'application/x-www-form-urlencoded' if not auth else 'application/json'
                with urlopen(Request(base + suffix, data=data, headers=headers), timeout=3) as response:
                    return response.read().decode()
            try:
                for _ in range(100):
                    try:
                        request()
                    except HTTPError:
                        break
                    except URLError:
                        time.sleep(.02)
                else:
                    self.fail('PHP did not start')
                day = datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()
                issued = int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Europe/Paris')).timestamp())
                values = links(newsletter_identity(day), 'https://example.org/feedback.php', 'x' * 32,
                               issued + 90 * 86400, briefing_date=day)
                query = '?' + urlsplit(values[0][1]).query
                html = request(query)
                self.assertFalse(list(pending.glob('*.json')), 'GET must not vote by default')
                forms = re.findall(r'name="token" value="([^"]+)"', html)
                self.assertEqual(len(forms), 3)
                request('?action=vote', urlencode({'token': forms[0]}).encode())
                stored = list(pending.glob('*.json'))
                self.assertEqual(len(stored), 1)
                row = json.loads(stored[0].read_text())
                validate_return(row)
                self.assertEqual(row['reason'], 'newsletter_balanced')
                # A new confirmation page represents a correction, with a new submission.
                html = request('?' + urlsplit(values[2][1]).query)
                forms = re.findall(r'name="token" value="([^"]+)"', html)
                request('?action=vote', urlencode({'token': forms[2]}).encode())
                rows = json.loads(request('?action=pull&limit=10', auth=True))['returns']
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[-1]['reason'], 'newsletter_too_ai')
                ids = [r['id'] for r in rows]
                ack = json.loads(request('?action=ack', json.dumps({'ids': ids[:1]}).encode(), auth=True))
                self.assertEqual(ack['deleted'], ids[:1])
                self.assertEqual(len(list(pending.glob('*.json'))), 1)
                # Existing optional GET mode and article protocol remain compatible.
                config.write_text(template.replace("'require_confirmation' => true", "'require_confirmation' => false"))
                article = links('b' * 64, 'https://example.org/feedback.php', 'x' * 32, issued + 90 * 86400)[0][1]
                request('?' + urlsplit(article).query)
                rows = json.loads(request('?action=pull&limit=10', auth=True))['returns']
                self.assertEqual(len(rows), 2)
                legacy = next(r for r in rows if r['reason'] == 'useful')
                self.assertNotIn('briefing_date', legacy)
                validate_return(legacy)
                request(query)
                request(query)
                rows = json.loads(request('?action=pull&limit=10', auth=True))['returns']
                self.assertEqual(len(rows), 3, 'Direct replay must remain idempotent')
                for row in rows:
                    validate_return(row)
                with self.assertRaises(HTTPError):
                    request(query[:-1] + ('0' if query[-1] != '0' else '1'))
                self.assertEqual(len(list(pending.glob('*.json'))), 3)
            finally:
                proc.terminate()
                proc.wait(timeout=5)
