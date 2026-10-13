import copy
import unittest

from app.import_feedback_archive import digest, presentations, source_key


def sample():
    sources = ['https://example.org/story?utm_source=rss']
    event = digest(dict(scope='legacy-feedback-test-v1', sources=sources))
    pid = digest(dict(event=event, briefing_date='2026-10-12', scope='legacy-feedback-test-v1'))
    row = dict(title='### 1. News', sources=sources, event_id=event, presentation_id=pid)
    return dict(schema_version=1, test_only=True,
                manifest=dict(date='2026-10-12', editions={lang: [copy.deepcopy(row)] for lang in ('en','fr')}),
                briefings={lang: '# Briefing\n\n### 1. News\n\nAvailable content.\n\n<!-- feedback:'+pid+' -->\n'
                           for lang in ('en','fr')})


class ArchiveTests(unittest.TestCase):
    def test_identity_and_actual_bilingual_content_preserved(self):
        archive = sample()
        day, result = presentations(archive)
        self.assertEqual(day, '2026-10-12')
        self.assertEqual(len(result), 1)
        item = next(iter(result.values()))
        self.assertEqual(set(item['editions']), {'en', 'fr'})
        self.assertIn('Available content.', item['editions']['en']['markdown'])
        self.assertNotIn('assessment', item)

    def test_mismatched_identity_or_missing_edition_rejected(self):
        for mutation in ('identity', 'content', 'edition', 'date', 'duplicate'):
            archive = sample()
            if mutation == 'identity': archive['manifest']['editions']['fr'][0]['event_id'] = '0'*64
            if mutation == 'content': archive['briefings']['en'] = '# Missing'
            if mutation == 'edition': del archive['manifest']['editions']['fr']
            if mutation == 'date': archive['manifest']['date'] = 'bad'
            if mutation == 'duplicate': archive['manifest']['editions']['en'] *= 2
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): presentations(archive)

    def test_source_matching_ignores_only_tracking_and_fragment(self):
        self.assertEqual(source_key('https://example.org/a?utm_source=x&id=1#top'),
                         source_key('https://example.org/a?id=1'))
        self.assertNotEqual(source_key('https://example.org/a?id=1'),
                            source_key('https://example.org/a?id=2'))
