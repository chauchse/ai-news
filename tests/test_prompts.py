import copy
from pathlib import Path
import ast
import unittest

from embeddings.prompts import render_prompt, validate_prompts, PROMPT_FIELDS
from project_config import load_section


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(load_section('briefing'))

    def test_all_configured_prompts_validate_and_render(self):
        validate_prompts(self.cfg)
        for name, fields in PROMPT_FIELDS.items():
            with self.subTest(name=name):
                result = render_prompt(self.cfg, name, **dict.fromkeys(fields, 'article {data}'))
                self.assertTrue(result.strip())
                if fields:
                    self.assertIn('article {data}', result)

    def test_custom_instruction_is_used_verbatim(self):
        self.cfg['prompts']['summarize_group_system'] = 'Write a factual summary.'
        self.assertEqual(render_prompt(self.cfg, 'summarize_group_system'), 'Write a factual summary.')

    def test_missing_empty_and_invalid_templates_are_rejected(self):
        for value in (None, '', '{unknown}', '{title.__class__}', '{title!r}', '{title', '{title}'):
            with self.subTest(value=value):
                self.cfg['prompts']['business_assessments_user'] = value
                with self.assertRaises(ValueError):
                    validate_prompts(self.cfg)

    def test_every_prompt_is_referenced_by_briefing(self):
        tree = ast.parse(Path('embeddings/briefing.py').read_text())
        referenced = {node.args[1].value for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id == 'render_prompt'}
        self.assertEqual(referenced, set(self.cfg['prompts']))


if __name__ == '__main__':
    unittest.main()
