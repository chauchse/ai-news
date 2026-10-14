"""Security grouping without loading the ML runtime."""
import ast
import copy
from pathlib import Path
import re
import sys
import unicodedata
import unittest
from unittest.mock import Mock

from project_config import load_section
from embeddings.prompts import render_prompt


def functions():
    tree = ast.parse(Path('embeddings/briefing.py').read_text())
    names = {'normalized_title', 'identifiers', 'distinctive_tokens',
             'vulnerability_identity', 'same_vulnerability',
             'deduplicate_security_events', 'applicability'}
    constants = {'VERSION_PATTERN', 'ZDI_PATTERN', 'STOPWORDS'}
    nodes = [n for n in tree.body if
             isinstance(n, ast.FunctionDef) and n.name in names or
             isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and
                 t.id in constants for t in n.targets)]
    ns = dict(render_prompt=render_prompt, re=re, sys=sys, unicodedata=unicodedata,
              extract_cves=lambda text: re.findall(r'CVE-\d{4}-\d+', text),
              group_category=lambda group: 'security')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<dedup>', 'exec'), ns)
    ns['ollama_object'] = Mock(return_value=dict(same_event=True, confidence=.9, evidence='Same advisory'))
    return ns


def article(product, suffix='actively exploited', cve=''):
    return dict(title=f'{product} critical remote execution vulnerability {suffix}',
                summary=cve, score=50)


class SecurityDeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.ns = functions()
        self.cfg = copy.deepcopy(load_section('briefing'))

    def dedup(self, a, b):
        return self.ns['deduplicate_security_events']([[a], [b]], self.cfg)

    def test_same_rules_for_known_and_unlisted_products(self):
        for product in ('NetScaler', 'UnlistedWidget'):
            with self.subTest(product=product):
                self.assertEqual(len(self.dedup(article(product), article(product, 'now actively exploited'))), 1)

    def test_distinct_advisories_never_reach_llm(self):
        for left, right in (('CVE-2026-1000', 'CVE-2026-2000'),
                            ('ZDI-CAN-1000', 'ZDI-CAN-2000')):
            self.assertEqual(len(self.dedup(article('Widget', cve=left), article('Widget', cve=right))), 2)
        self.ns['ollama_object'].assert_not_called()

    def test_shared_vendor_alone_is_insufficient(self):
        self.assertEqual(len(self.dedup(article('NetScaler'),
            dict(title='NetScaler authentication bypass advisory', summary='', score=50))), 2)
        self.ns['ollama_object'].assert_not_called()

    def test_configurable_threshold_and_validation(self):
        self.cfg['briefing']['security_deduplication']['min_shared_title_tokens'] = 100
        self.assertEqual(len(self.dedup(article('Widget'), article('Widget'))), 2)
        self.cfg['briefing']['security_deduplication']['min_confidence'] = 2
        with self.assertRaises(ValueError):
            self.dedup(article('Widget'), article('Widget'))

    def test_confidence_required_and_exposure_unknown(self):
        self.ns['ollama_object'].return_value['confidence'] = .5
        self.assertEqual(len(self.dedup(article('Widget'), article('Widget'))), 2)
        self.assertEqual(self.ns['applicability']([article('NetScaler')], self.cfg), ('unknown', []))


if __name__ == '__main__':
    unittest.main()
