"""Shared access to the root application configuration (no secrets)."""
import os
from pathlib import Path
import yaml

DEFAULT_PATH = Path(__file__).resolve().with_name('config.yaml')


def config_path():
    return Path(os.getenv('AI_NEWS_CONFIG') or DEFAULT_PATH)


def load_section(name, path=None):
    """Explicit legacy inputs remain readable; production uses only config.yaml."""
    source = Path(path) if path is not None else config_path()
    data = yaml.safe_load(source.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Configuration must be a mapping')
    if 'config_version' not in data:
        if path is None:
            raise ValueError('Global configuration version missing')
        return data  # Compatibility for existing CLI inputs and test fixtures.
    if type(data['config_version']) is not int or data['config_version'] != 1:
        raise ValueError('Unsupported configuration version')
    value = data.get(name)
    if not isinstance(value, dict):
        raise ValueError('Missing configuration section: ' + name)
    return value


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('section')
    parser.add_argument('key')
    args = parser.parse_args()
    value = load_section(args.section)[args.key]
    if not isinstance(value, (str, int, float, bool)):
        parser.error('Only scalar values can be printed')
    print(value)
