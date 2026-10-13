"""Daily balance feedback stays separate from article annotations."""
import os
import re
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from mailer.send import add_feedback, render_html
from mailer.feedback_links import (NEWSLETTER_REASONS, REASONS, links, newsletter_identity,
                                   verify, validate_return)
from feedback.importer import Store, synchronize

TEXT = ('# Briefing — 2026-10-13\n\n## News\n\n### 1. Title\nSummary\n'
        '**Sources :**\n- [Source](https://example.org/article)\n')
ENV = {'FEEDBACK_URL': 'https://example.org/feedback/feedback.php', 'FEEDBACK_SIGNING_SECRET': 'x' * 32}


def returned(reason='newsletter_balanced'):
    value = dict(schema_version=1, id='a' * 64, submission_id='a' * 64,
                 presentation_id=newsletter_identity('2026-10-13'), briefing_date='2026-10-13',
                 reason=reason, submitted_at='2026-10-13T00:00:00.000000Z',
                 received_at='2026-10-13T00:00:01.000000Z')
    return value


@patch.dict(os.environ, ENV)
class NewsletterMailTests(unittest.TestCase):
    def payloads(self, text):
        return [verify(parse_qs(urlsplit(url).query)['token'][0], 'x' * 32)
                for url in re.findall(r'\]\((https://example.org/feedback/[^\s]+)\)', text)]

    def test_opt_in_three_buttons_and_article_links_unchanged(self):
        old = add_feedback(TEXT, {})
        result = add_feedback(TEXT, {'feedback': {'newsletter_enabled': True}})
        self.assertTrue(result.startswith(old))
        payloads = self.payloads(result)
        self.assertEqual([p['reason'] for p in payloads], list(REASONS + NEWSLETTER_REASONS))
        self.assertEqual(self.payloads(old), payloads[:6])
        self.assertEqual(render_html(result).count('display:inline-block'), 9)
        self.assertIn('Équilibrée', result)
        self.assertNotEqual(payloads[0]['presentation_id'], payloads[-1]['presentation_id'])

    def test_same_day_common_en_fr_identity_and_different_day(self):
        cfg = {'feedback': {'newsletter_enabled': True}}
        fr = self.payloads(add_feedback(TEXT, cfg))[-3:]
        en = self.payloads(add_feedback(TEXT.replace('Title', 'English title'), dict(cfg, language='en')))[-3:]
        self.assertEqual(fr, en)
        self.assertNotEqual(newsletter_identity('2026-10-12'), fr[0]['presentation_id'])

    def test_disabled_feedback_and_type_validation(self):
        self.assertEqual(add_feedback(TEXT, {'feedback': {'enabled': False, 'newsletter_enabled': True}}), TEXT)
        with self.assertRaises(ValueError):
            add_feedback(TEXT, {'feedback': {'newsletter_enabled': 'true'}})

    def test_return_contract_and_scope_rejection(self):
        row = returned()
        self.assertEqual(validate_return(row), row)
        for changed in (dict(row, reason='useful'), dict(row, briefing_date='2026-10-12'),
                        {k: v for k, v in row.items() if k != 'briefing_date'}):
            with self.subTest(row=changed), self.assertRaises(ValueError):
                validate_return(changed)
        legacy = dict(row, reason='useful', presentation_id='b' * 64)
        del legacy['briefing_date']
        self.assertEqual(validate_return(legacy), legacy)


class NewsletterImportTests(unittest.TestCase):
    def test_separate_table_and_replay_conflict(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        row = returned()
        cursor.fetchone.return_value = None
        self.assertEqual(Store(conn).save([row]), ([row['id']], 1))
        self.assertIn('INSERT INTO feedback_newsletter_returns', cursor.execute.call_args.args[0])
        cursor.fetchone.return_value = (row,)
        self.assertEqual(Store(conn).save([row]), ([row['id']], 0))
        self.assertEqual(Store(conn).save([dict(row, reason='newsletter_too_ai')]), ([], 0))
        legacy = dict(row, reason='useful', presentation_id='b' * 64)
        del legacy['briefing_date']
        cursor.fetchone.return_value = None
        Store(conn).save([legacy])
        self.assertIn('INSERT INTO feedback_returns', cursor.execute.call_args.args[0])

    def test_no_ack_on_storage_failure_and_mixed_batch(self):
        cfg = dict(batch_size=10, max_batches=1)
        row = returned()
        legacy = dict(row, id='b' * 64, submission_id='b' * 64, reason='useful')
        del legacy['briefing_date']
        client, store = MagicMock(), MagicMock()
        store.lock.side_effect = nullcontext
        client.request.return_value = dict(returns=[row, legacy])
        store.save.side_effect = RuntimeError('storage failed')
        with self.assertRaises(RuntimeError):
            synchronize(client, store, cfg)
        self.assertEqual(client.request.call_count, 1)
        store.save.side_effect = None
        store.save.return_value = ([row['id'], legacy['id']], 2)
        client.request.side_effect = [dict(returns=[row, legacy]), dict(deleted=[row['id'], legacy['id']], absent=[], errors=[])]
        result = synchronize(client, store, cfg)
        self.assertEqual(result['confirmed'], 2)
