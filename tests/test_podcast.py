import datetime as dt
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from podcast.publish import DATE_RE, append_episode, write_feed


class PodcastHistoryTests(unittest.TestCase):
    @patch('podcast.publish.get_audio_duration', return_value=None)
    def test_distinct_episodes_same_day_survive_restart_and_retry(self, duration):
        now = dt.datetime(2026, 10, 10, 10, tzinfo=ZoneInfo('Europe/Paris'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / '2026-10-10-briefing.mp3'
            audio.write_bytes(b'first recording')
            state, first = append_episode([], audio, [], now, 7)
            state_file = root / 'episodes.json'
            state_file.write_text(json.dumps(state))
            state = json.loads(state_file.read_text())
            audio.write_bytes(b'second recording')
            later = now + dt.timedelta(hours=1)
            state, second = append_episode(state, audio, [], later, 7)
            self.assertEqual(len(state), 2)
            self.assertNotEqual(first['filename'], second['filename'])
            state, retry = append_episode(state, audio, [], later + dt.timedelta(minutes=1), 7)
            self.assertEqual(len(state), 2)
            self.assertEqual(retry['published_at'], later.isoformat())
            cfg = {'site_url': 'https://example.com/', 'cover': 'cover.jpg',
                   'podcast': {'title': 'News', 'description': 'Briefing', 'author': 'AI News',
                               'email': 'contact@example.com'}}
            feed = root / 'podcast.xml'
            write_feed(cfg, state, feed)
            items = ET.parse(feed).findall('./channel/item')
            self.assertEqual(len(items), 2)
            self.assertEqual(len({item.findtext('guid') for item in items}), 2)
            self.assertTrue(items[0].find('enclosure').get('url').endswith(second['filename']))

    @patch('podcast.publish.get_audio_duration', return_value=None)
    def test_retention_keeps_legacy_entries_within_window(self, duration):
        now = dt.datetime(2026, 10, 10, tzinfo=ZoneInfo('Europe/Paris'))
        state = [{'date': date, 'filename': date + '-briefing.mp3',
                  'published_at': date + 'T10:00:00+02:00'}
                 for date in ['2026-10-03', '2026-10-04']]
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'audio.mp3'
            audio.write_bytes(b'audio')
            state, episode = append_episode(state, audio, [], now, 7)
            self.assertEqual({ep['date'] for ep in state}, {'2026-10-04', '2026-10-10'})
            self.assertIsNotNone(DATE_RE.fullmatch(episode['filename']))
            self.assertIsNotNone(DATE_RE.fullmatch('2026-10-04-briefing.mp3'))
            self.assertIsNone(DATE_RE.fullmatch('other.mp3'))


if __name__ == '__main__':
    unittest.main()
