"""One configured domain priority feeds both scoring consumers."""
import tempfile
from pathlib import Path
import unittest
import yaml
from project_config import load_section


class DomainWeightsTests(unittest.TestCase):
    def config(self):
        return yaml.safe_load(Path('config.yaml').read_text())

    def load(self, data, section):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            path.write_text(yaml.safe_dump(data))
            return load_section(section, path)

    def test_shared_weights_and_no_duplicate_definitions(self):
        data = self.config()
        self.assertNotIn('domains', data['briefing']['scoring'])
        self.assertTrue(all('weight' not in d for d in data['semantic_scoring']['domains']))
        self.assertTrue(all('max_points' in t and 'weight' not in t for t in data['scoring']['topics']))
        data['scoring']['domain_weights']['cybersecurity'] = 37
        business = self.load(data, 'briefing')['scoring']['domains']
        semantic = self.load(data, 'semantic_scoring')['domains']
        self.assertEqual(business['cybersecurity']['weight'], 37)
        self.assertEqual(next(d['weight'] for d in semantic if d['name'] == 'cybersecurity'), 37)

    def test_invalid_and_missing_weights_rejected(self):
        for weight in (-1, True, '25', float('nan'), float('inf')):
            data = self.config()
            data['scoring']['domain_weights']['cybersecurity'] = weight
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                self.load(data, 'briefing')
        data = self.config()
        del data['scoring']['domain_weights']['cybersecurity']
        with self.assertRaisesRegex(ValueError, 'Missing domain weight'):
            self.load(data, 'semantic_scoring')

    def test_duplicate_local_weights_rejected(self):
        data = self.config()
        data['semantic_scoring']['domains'][0]['weight'] = 99
        with self.assertRaises(ValueError):
            self.load(data, 'semantic_scoring')
        data = self.config()
        data['briefing']['scoring']['domains'] = {}
        with self.assertRaises(ValueError):
            self.load(data, 'briefing')


if __name__ == '__main__':
    unittest.main()
