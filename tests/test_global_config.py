"""Configuration migration and cross-module behavior, without production effects."""
import ast
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from project_config import config_path, load_section
from app.source_health import disable_source
from embeddings.glossary import CyberGlossary
from embeddings.watchlist import load_rules, evaluate
from mailer.send import load_config, build_message
from podcast.publish import config_load, edition_url


ROOT = Path(__file__).resolve().parents[1]
GLOBAL = ROOT / 'config.yaml'
SECTIONS = ('collection', 'scoring', 'briefing', 'semantic_scoring', 'watchlists',
            'glossary', 'audio', 'podcast', 'mail')


class GlobalConfigurationTests(unittest.TestCase):
    def test_all_sections_have_distinct_data(self):
        data = yaml.safe_load(GLOBAL.read_text())
        self.assertEqual(data['config_version'], 1)
        self.assertEqual(set(data) - {'config_version'}, set(SECTIONS))
        for name in SECTIONS:
            self.assertEqual(load_section(name, GLOBAL), data[name])

    def test_default_independent_of_working_directory_and_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'alternate.yaml'
            path.write_text('config_version: 1\naudio:\n  engine: piper\n')
            with patch.dict(os.environ, {'AI_NEWS_CONFIG': str(path)}):
                self.assertEqual(config_path(), path)
                self.assertEqual(load_section('audio')['engine'], 'piper')
            with patch.dict(os.environ, {'AI_NEWS_CONFIG': ''}):
                self.assertEqual(config_path(), GLOBAL)

    def test_mutating_runtime_config_does_not_change_file_or_other_loads(self):
        before = GLOBAL.read_bytes()
        cfg = load_section('audio', GLOBAL)
        cfg['voices'].clear()
        self.assertTrue(load_section('audio', GLOBAL)['voices'])
        self.assertEqual(GLOBAL.read_bytes(), before)

    def test_legacy_explicit_input_and_invalid_globals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            path.write_text('engine: piper\n')
            self.assertEqual(load_section('audio', path), {'engine': 'piper'})
            with patch.dict(os.environ, {'AI_NEWS_CONFIG': str(path)}):
                with self.assertRaises(ValueError): load_section('audio')
                with self.assertRaises(ValueError): load_section('audio', path)
            for content in ('[]', 'config_version: 2\naudio: {}',
                            'config_version: true\naudio: {}', 'config_version: 1\nmail: {}'):
                path.write_text(content)
                with self.assertRaises(ValueError): load_section('audio', path)

    def test_global_retirement_only_changes_requested_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            original = GLOBAL.read_text()
            path.write_text(original)
            before = yaml.safe_load(original)
            url = next(s['url'] for s in before['collection']['sources'] if s.get('enabled', True))
            expected = copy.deepcopy(before)
            for source in expected['collection']['sources']:
                if source['url'] == url: source['enabled'] = False
            disable_source(path, url)
            self.assertEqual(yaml.safe_load(path.read_text()), expected)
            self.assertEqual(path.with_suffix('.yaml.bak').read_text(), original)
            self.assertIn('# AI News', path.read_text())
            self.assertIn('# Traduction', path.read_text())
            self.assertEqual(load_section('collection', path), expected['collection'])
            disable_source(path, url)
            self.assertEqual(path.with_suffix('.yaml.bak').read_text(), original)

    def test_failed_atomic_retirement_preserves_global(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            original = GLOBAL.read_bytes(); path.write_bytes(original)
            url = next(s['url'] for s in load_section('collection', path)['sources'] if s.get('enabled', True))
            with patch.object(Path, 'replace', side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError): disable_source(path, url)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob('.config-*.tmp')), [])

    def test_mail_global_and_legacy_produce_same_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / 'old-mail.yaml'
            legacy.write_text(yaml.safe_dump(load_section('mail', GLOBAL)))
            briefing = Path(directory) / 'briefing.md'; briefing.write_text('# News\n\nA source.')
            with patch.dict(os.environ, {'SENDER': 'news@example.org', 'RECIPIENT': 'reader@example.org'}):
                first = load_config(GLOBAL); second = load_config(legacy)
                self.assertEqual(first, second)
                for cfg in (first, second): cfg['input'] = str(briefing)
                a, b = build_message(first), build_message(second)
                for subtype in ('plain', 'html'):
                    self.assertEqual(a.get_body(preferencelist=(subtype,)).get_content(),
                                     b.get_body(preferencelist=(subtype,)).get_content())

    def test_podcast_global_preserves_editions_and_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / 'old-podcast.yaml'
            legacy.write_text(yaml.safe_dump(load_section('podcast', GLOBAL)))
            with patch.dict(os.environ, {'PODCAST_SITE_URL': 'https://example.org/podcasts',
                                        'PODCAST_SFTP_REMOTE_PATH': '/private/site'}):
                current, old = config_load(GLOBAL), config_load(legacy)
                self.assertEqual(current, old)
                for suffix in ('en', 'fr'):
                    self.assertEqual(edition_url(current['site_url'], suffix),
                                     edition_url(old['site_url'], suffix))

    def test_watchlists_and_glossary_preserve_existing_interfaces(self):
        rules = load_rules(GLOBAL)
        self.assertEqual(rules, load_section('watchlists', GLOBAL))
        first = evaluate('Ubuntu and Microsoft 365', 'News', config=rules)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.yaml'; path.write_text(yaml.safe_dump(rules))
            self.assertEqual(evaluate('Ubuntu and Microsoft 365', 'News', config=load_rules(path)), first)
        glossary = CyberGlossary(GLOBAL)
        text = 'ANSSI and CVE-2026-12345 on Ubuntu 26.10'
        protected, terms = glossary.protect(text)
        self.assertEqual(glossary.restore(protected, terms), text)

    def test_briefing_and_profiles_read_global_without_model_import(self):
        tree = ast.parse((ROOT / 'embeddings/briefing.py').read_text())
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('config_load', 'load_domain_profiles')]
        ns = dict(load_section=load_section, config_path=config_path, CONFIG_PATH=GLOBAL, Path=Path, os=os)
        exec(compile(ast.Module(body=funcs, type_ignores=[]), '<configuration>', 'exec'), ns)
        self.assertEqual(ns['config_load'](), load_section('briefing', GLOBAL))
        descriptions, names, slices = ns['load_domain_profiles']()
        expected = load_section('semantic_scoring', GLOBAL)['domains']
        self.assertEqual(names, [d['name'].lower() for d in expected])
        self.assertEqual(descriptions, [s for d in expected for s in d['descriptions']])
        self.assertEqual(slices[-1][1], len(descriptions))

    def test_container_mounts_and_shared_loader_available(self):
        compose = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
        services = compose['services']
        self.assertIn('./:/workspace', services['collector']['volumes'])
        self.assertEqual(services['collector']['environment']['AI_NEWS_CONFIG'], '/workspace/config.yaml')
        for name in ('embeddings', 'audio', 'xtts', 'podcast', 'mailer'):
            self.assertIn('./config.yaml:/app/config.yaml:ro', services[name]['volumes'])
        for filename in ('Dockerfile', 'embeddings/Dockerfile', 'audio/Dockerfile',
                         'audio/xtts/Dockerfile', 'mailer/Dockerfile', 'podcast/Dockerfile'):
            self.assertIn('COPY project_config.py /app/project_config.py', (ROOT / filename).read_text())


if __name__ == '__main__':
    unittest.main()
