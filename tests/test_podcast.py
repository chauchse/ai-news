import datetime as dt
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from podcast.publish import DATE_RE, append_episode, write_feed, config_load, edition_url, parse_articles, episode_description, CONTENT, ITUNES


class PodcastHistoryTests(unittest.TestCase):
    def test_markdown_source_headers_keep_links_in_descriptions(self):
        for header in ('**Sources :**', '**Sources** :', 'Sources:', '**Source:**'):
            with self.subTest(header=header):
                text = f'### 1. Title\nSummary\n{header}\n- [Original article](https://example.com/news)\n'
                articles = parse_articles(text)
                self.assertEqual(articles[0]['sources'],
                                 [{'name': 'Original article', 'url': 'https://example.com/news'}])
                self.assertIn('https://example.com/news', episode_description(articles))

    @patch('podcast.publish.get_audio_duration', return_value=None)
    def test_republish_repairs_description_without_adding_episode(self, duration):
        now = dt.datetime(2026, 10, 11, 4, tzinfo=ZoneInfo('Europe/Paris'))
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'briefing.mp3'
            audio.write_bytes(b'audio')
            state, episode = append_episode([], audio, [{'title': 'Title', 'sources': []}], now, 7)
            published = episode['published_at']
            articles = parse_articles('### 1. Title\n**Sources :**\n- [Source](https://example.com/news)\n')
            state, updated = append_episode(state, audio, articles, now + dt.timedelta(hours=1), 7)
            self.assertEqual(len(state), 1)
            self.assertEqual(updated['published_at'], published)
            self.assertIn('https://example.com/news', updated['description'])

    def test_edition_urls_preserve_base_path_and_normalize_trailing_slash(self):
        for base in ('https://example.com/podcast', 'https://example.com/podcast/'):
            for language in ('en', 'fr'):
                self.assertEqual(edition_url(base, language), f'https://example.com/podcast/{language}/')
        for invalid in ('', 'ftp://example.com', 'https://example.com/?x=1', 'https://user:pass@example.com'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                edition_url(invalid, 'fr')

    @patch('podcast.publish.get_audio_duration', return_value=None)
    def test_feed_description_language_and_line_breaks(self, duration):
        articles = [{'title': 'News & analysis', 'sources': [{'name': 'Source', 'url': 'https://example.com'}]}]
        now = dt.datetime(2026, 10, 11, 4, tzinfo=ZoneInfo('Europe/Paris'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language in ('en', 'fr'):
                with self.subTest(language=language):
                    audio = root / f'briefing-{language}.mp3'
                    audio.write_bytes(language.encode())
                    state, episode = append_episode([], audio, articles, now, 7, language)
                    cfg = {'site_url': f'https://example.com/{language}/', 'cover': 'cover.jpg',
                           'podcast': {'title': 'News', 'description': 'Daily news', 'author': 'News',
                                       'email': 'contact@example.com', 'language': language}}
                    feed = root / f'{language}.xml'
                    write_feed(cfg, state, feed)
                    item = ET.parse(feed).find('channel/item')
                    self.assertIn(f'[{language.upper()}]', item.findtext('title'))
                    description = item.findtext('description')
                    self.assertIn('<br/>', description)
                    self.assertIn('News &amp; analysis', description)
                    self.assertEqual(item.findtext(f'{{{CONTENT}}}encoded'), description)
                    plain = item.findtext(f'{{{ITUNES}}}summary')
                    self.assertIn('In this briefing:' if language == 'en' else 'Au sommaire', plain)
                    self.assertIn('\n', plain)
                    self.assertIn(f'/{language}/episodes/', item.find('enclosure').get('url'))

    @patch.dict('os.environ', {'PODCAST_SFTP_HOST': 'sftp.example.com',
                              'PODCAST_SFTP_PORT': '2222',
                              'PODCAST_SFTP_REMOTE_PATH': '/podcast',
                              'PODCAST_EMAIL': 'contact@example.com',
                              'PODCAST_SITE_URL': 'https://example.com/podcasts/'})
    def test_connection_and_contact_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'podcast.yaml'
            config.write_text('podcast:\n  title: News\nsftp: {}\n')
            cfg = config_load(config)
            self.assertEqual(cfg['sftp']['host'], 'sftp.example.com')
            self.assertEqual(cfg['sftp']['port'], '2222')
            self.assertEqual(cfg['sftp']['remote_path'], '/podcast')
            self.assertEqual(cfg['podcast']['email'], 'contact@example.com')
            self.assertEqual(cfg['site_url'], 'https://example.com/podcasts/')

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
