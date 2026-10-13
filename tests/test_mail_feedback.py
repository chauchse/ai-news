import os
import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from mailer.send import add_feedback, render_html
from mailer.feedback_links import verify, REASONS


@patch.dict(os.environ, {'FEEDBACK_URL': 'https://example.com/feedback/feedback.php',
                         'FEEDBACK_SIGNING_SECRET': 'x' * 32})
class FeedbackMailTests(unittest.TestCase):
    text = ('# Briefing — 2026-10-13\n\n## News\n\n### 1. Title\n\nSummary\n\n'
            '**Sources :**\n- [Source](https://example.org/news)\n\n'
            '## Other security alerts\n\n- [Annex](https://example.org/annex)\n')

    def payloads(self, text):
        return [verify(parse_qs(urlsplit(url).query)['token'][0], 'x' * 32)
                for url in re.findall(r'\]\((https://example.com/[^\s]+)\)', text)]

    def test_six_signed_buttons_html_text_and_annex(self):
        result = add_feedback(self.text, {})
        payloads = self.payloads(result)
        self.assertEqual([p['reason'] for p in payloads], list(REASONS))
        self.assertEqual(len({p['presentation_id'] for p in payloads}), 1)
        self.assertLess(result.index('Votre avis'), result.index('## Other'))
        self.assertEqual(render_html(result).count('display:inline-block'), 6)
        self.assertIn('[Source](https://example.org/news)', result)
        self.assertEqual(result, add_feedback(self.text, {}))

    def test_identity_shared_between_languages(self):
        fr = self.payloads(add_feedback(self.text, {}))
        en = self.payloads(add_feedback(self.text.replace('Title', 'Titre').replace('Summary', 'Résumé'), {'language': 'en'}))
        self.assertEqual(fr, en)

    def test_old_markers_preserved_in_token(self):
        text = self.text.replace('## Other', '<!-- feedback:' + 'a' * 64 + ' -->\n\n## Other')
        self.assertEqual(self.payloads(add_feedback(text, {}))[0]['presentation_id'], 'a' * 64)

    def test_disable_and_missing_secret(self):
        self.assertEqual(add_feedback(self.text, {'feedback': {'enabled': False}}), self.text)
        with patch.dict(os.environ, {'FEEDBACK_URL': ''}):
            self.assertEqual(add_feedback(self.text, {}), self.text)
        with patch.dict(os.environ, {'FEEDBACK_SIGNING_SECRET': ''}):
            with self.assertRaises(ValueError):
                add_feedback(self.text, {})

    def test_expired_and_missing_sources_rejected(self):
        with self.assertRaises(ValueError):
            add_feedback(self.text.replace('2026-10-13', '2020-01-01'), {})
        with self.assertRaises(ValueError):
            add_feedback(self.text.replace('- [Source](https://example.org/news)', ''), {})


    def test_direct_vote_date_not_future_including_dst(self):
        # The historical PHP direct-vote path computes exp - link_seconds.
        # Regression: the old extra calendar day placed that date in the future.
        for day in ('2026-10-13', '2026-03-20', '2026-10-20'):
            with self.subTest(day=day):
                midnight = int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Europe/Paris')).timestamp())
                text = self.text.replace('2026-10-13', day)
                with patch('mailer.send.time.time', return_value=midnight + 60):
                    result = add_feedback(text, {})
                    tokens = re.findall(r'\]\((https://example.com/[^\s]+)\)', result)
                    for url in tokens:
                        token = parse_qs(urlsplit(url).query)['token'][0]
                        payload = verify(token, 'x' * 32, now=midnight + 60)
                        submitted_at = payload['exp'] - 90 * 86400
                        self.assertEqual(submitted_at, midnight)
                        self.assertLessEqual(submitted_at, midnight + 60)

    def test_future_briefing_refused_before_sending(self):
        with patch('mailer.send.time.time', return_value=0):
            with self.assertRaisesRegex(ValueError, 'future'):
                add_feedback(self.text, {})
