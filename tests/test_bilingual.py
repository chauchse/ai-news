"""Exercise briefing orchestration without downloading ML models."""
import ast
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from audio.tts import extract
from mailer.send import render_html
from podcast.publish import parse_articles, episode_description


def briefing_functions():
    tree = ast.parse(Path('embeddings/briefing.py').read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('main', 'format_markdown', 'french_fields', 'ensure_english')]
    namespace = dict(json=json, datetime=datetime, timezone=timezone, Path=Path, os=os, time=time)
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<briefing>', 'exec'), namespace)
    return namespace


class BilingualTests(unittest.TestCase):
    def test_french_generated_content_is_corrected_without_changing_english_field(self):
        ns = briefing_functions()
        content = {'title': 'Google Home Speaker avec Gemini Intelligence Artificielle Testé et Pris',
                   'summary': 'Google has launched a new smart speaker with Gemini artificial intelligence.'}
        ns['ollama_json'] = Mock(return_value={'title': 'Google Home Speaker with Gemini AI reviewed',
                                               'summary': 'This must not replace the English summary.'})
        result = ns['ensure_english'](content, {'llm': {'model': 'test'}})
        self.assertEqual(result['title'], 'Google Home Speaker with Gemini AI reviewed')
        self.assertEqual(result['summary'], content['summary'])
        ns['ollama_json'].assert_called_once()

    def test_english_content_needs_no_extra_llm_call(self):
        ns = briefing_functions()
        ns['ollama_json'] = Mock()
        content = {'title': 'Ubuntu 26.10 to Include Rust-based GnuPG Replacement',
                   'summary': 'Canonical is introducing a new OpenPGP implementation written in Rust.'}
        self.assertEqual(ns['ensure_english'](content, {'llm': {'model': 'test'}}), content)
        ns['ollama_json'].assert_not_called()

    def test_persistent_french_is_rejected_after_bounded_retries(self):
        ns = briefing_functions()
        content = {'title': 'Une nouvelle enceinte connectée avec une intelligence artificielle',
                   'summary': 'Après des années d’immobilité, Google lance une nouvelle enceinte connectée avec l’intelligence artificielle Gemini.'}
        ns['ollama_json'] = Mock(return_value=content)
        with self.assertRaises(ValueError):
            ns['ensure_english'](content, {'llm': {'model': 'test'}})
        self.assertEqual(ns['ollama_json'].call_count, 2)

    def test_single_synthesis_same_sources_and_order(self):
        ns = briefing_functions()
        source = {'id': 1, 'title': 'Une source française', 'url': 'https://example.com/fr', 'score': 50}
        assessment = {'group': [source], 'category': 'security', 'business': 3,
                      'importance': 4, 'applicability': 'unknown'}
        ns.update(config_load=lambda: {'briefing': {'language': 'fr', 'languages': ['en', 'fr']}},
                  fetch_articles=lambda cfg: [source], group_articles=lambda a: [a],
                  rank_groups=lambda g: g, deduplicate_security_events=lambda g, c: g,
                  select_groups=lambda g, c: ([assessment], []),
                  filter_previous_briefings=lambda g, c: g, embedding_model=Mock(),
                  summarize_group=Mock(return_value={'title': 'English title', 'summary': 'English summary.'}),
                  Translator=Mock(return_value=Mock(translate=lambda t: 'FR ' + t)))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'BRIEFING_OUTPUT': directory + '/briefing.md', 'BRIEFING_DRY_RUN': '0'}):
            ns['main']()
            en = (Path(directory) / 'briefing-en.md').read_text()
            fr = (Path(directory) / 'briefing-fr.md').read_text()
            self.assertIn('Priority security alerts', en)
            self.assertNotIn('Pertinence métier', en)
            self.assertIn('FR English title', fr)
            for text in (en, fr):
                self.assertIn('[Une source française](https://example.com/fr)', text)
            ns['summarize_group'].assert_called_once()
            ns['Translator'].assert_called_once()
            with patch.dict(os.environ, {'BRIEFING_DAILY_DIR': directory + '/daily/2026-10-11'}):
                ns['main']()
            for language in ('en', 'fr'):
                path = Path(directory) / 'daily' / '2026-10-11' / language / 'briefing.md'
                self.assertTrue(path.is_file())
                self.assertIn('https://example.com/fr', path.read_text())

    def test_sections_are_grouped_once_with_stable_order_in_both_languages(self):
        formatter = briefing_functions()['format_markdown']
        events = [dict(generated={'title': title, 'summary': 'Summary.'}, sources=[], score=50,
                       assessment={'category': category, 'business': 3, 'importance': 4,
                                   'applicability': 'unknown'})
                  for title, category in [('Strategy A', 'strategic'), ('Security A', 'security'),
                                          ('Strategy B', 'strategic'), ('Security B', 'security')]]
        for language, heading in [('en', 'Strategic and business news'),
                                  ('fr', 'Veille stratégique et métier')]:
            with self.subTest(language=language):
                text = formatter(events, {'briefing': {'language': language}}, 4, 4)
                self.assertEqual(text.count('## ' + heading + '\n'), 1)
                self.assertEqual([item[1] for item in extract(text)],
                                 ['Security A', 'Security B', 'Strategy A', 'Strategy B'])
                self.assertIn('### 4. Strategy B', text)
        self.assertEqual(events[0]['generated']['title'], 'Strategy A')

    def test_english_annex_is_not_spoken_or_attached_to_last_article(self):
        markdown = ('## Strategic and business news\n### 1. Title\nSummary.\n'
                    '**Sources :**\n- [Source](https://example.com)\n'
                    '## Other security alerts to review\n- [Extra](https://extra.example.com)\n')
        self.assertEqual(len(extract(markdown)), 1)
        articles = parse_articles(markdown)
        self.assertEqual(len(articles[0]['sources']), 1)
        self.assertIn('In this briefing:', episode_description(articles, 'en'))
        self.assertIn('lang="en"', render_html(markdown, 'en'))


if __name__ == '__main__':
    unittest.main()
