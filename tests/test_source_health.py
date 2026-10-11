import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from app.source_health import disable_source, consecutive_failures


class SourceHealthTests(unittest.TestCase):
    def test_retirement_preserves_comments_and_other_sources_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sources.yaml'
            original = '# Catalogue\nsources:\n- name: A\n  url: https://a.example\n  enabled: true\n- name: B\n  url: https://b.example\n  enabled: true\n'
            path.write_text(original)
            disable_source(path, 'https://a.example')
            data = yaml.safe_load(path.read_text())
            self.assertFalse(data['sources'][0]['enabled'])
            self.assertTrue(data['sources'][1]['enabled'])
            self.assertIn('# Catalogue', path.read_text())
            self.assertEqual(path.with_suffix('.yaml.bak').read_text(), original)
            disable_source(path, 'https://a.example')
            self.assertEqual(path.with_suffix('.yaml.bak').read_text(), original)

    def test_only_consecutive_feed_failures_trigger_retirement(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        for statuses, expected in [([('failed', '[feed] timeout')] * 3, True),
                                   ([('failed', '[feed] timeout')] * 2, False),
                                   ([('failed', '[feed] timeout'), ('success', None), ('failed', '[feed] error')], False),
                                   ([('failed', '[internal] SQL')] * 3, False),
                                   ([('failed', 'legacy error')] * 3, False)]:
            cur.fetchall.return_value = statuses
            self.assertEqual(consecutive_failures(conn, 42, 3), expected)
