import ast
import re
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import numpy as np
# Exercise the actual functions without importing/downloading the ML runtime.
_tree = ast.parse(Path('embeddings/briefing.py').read_text())
_names = {'canonical_url', 'read_events', 'previous_events', 'exclude_previous'}
_nodes = [node for node in _tree.body
          if (isinstance(node, ast.FunctionDef) and node.name in _names)
          or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
              and target.id in {'HISTORY_HEADING', 'HISTORY_LINK'} for target in node.targets))]
_namespace = dict(Path=Path, re=re, timedelta=timedelta, urlsplit=urlsplit,
                  urlunsplit=urlunsplit, parse_qsl=parse_qsl, urlencode=urlencode)
exec(compile(ast.Module(body=_nodes, type_ignores=[]), '<briefing-history>', 'exec'), _namespace)
previous_events = _namespace['previous_events']
exclude_previous = _namespace['exclude_previous']


class BriefingHistoryTests(unittest.TestCase):
    def test_missing_history_and_preferred_edition_and_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(previous_events(root, date(2026, 10, 11)), [])
            for day, language, title in [('2026-10-10', 'fr', 'Français'), ('2026-10-10', 'en', 'English'),
                                         ('2026-10-11', 'en', 'Today'), ('2026-10-03', 'en', 'Expired')]:
                path = root / day / language / 'briefing.md'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'## News\n### 1. {title}\nSummary.\n**Sources :**\n- [Source](https://example.com/news)\n')
            events = previous_events(root, date(2026, 10, 11))
            self.assertEqual([e['title'] for e in events], ['English'])
            self.assertEqual(events[0]['urls'], ['https://example.com/news'])

    def test_no_history_does_not_load_model_or_call_llm(self):
        factory, review = Mock(), Mock()
        groups = [[{'title': 'News'}]]
        self.assertIs(exclude_previous(groups, [], factory, review), groups)
        factory.assert_not_called()
        review.assert_not_called()

    def test_exact_source_excluded_without_llm(self):
        factory, review = Mock(), Mock()
        groups = [[{'title': 'News', 'url': 'https://example.com/news?utm_source=rss'}]]
        history = [{'title': 'Old', 'summary': '', 'urls': ['https://example.com/news']}]
        self.assertEqual(exclude_previous(groups, history, factory, review), [])
        factory.assert_not_called()

    def test_multilingual_event_repetition_update_and_distinct_topic(self):
        groups = [[{'title': 'Nouvelle source française', 'summary': 'Un événement', 'url': 'https://new.example.com'}]]
        history = [{'title': 'English event', 'summary': 'Already covered', 'urls': []}]
        model = Mock()
        model.encode.return_value = np.array([[1., 0.], [1., 0.]])
        for same, update, expected in [(True, False, []), (True, True, groups), (False, False, groups)]:
            review = Mock(return_value={'same_event': same, 'material_update': update})
            self.assertEqual(exclude_previous(groups, history, lambda: model, review), expected)
            review.assert_called_once()
