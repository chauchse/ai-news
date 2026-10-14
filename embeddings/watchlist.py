"""User-configurable watch/ignore rules. No DB writes."""
import os
import re
from pathlib import Path
from project_config import config_path, load_section

PATH = Path(os.getenv('WATCHLIST_FILE') or config_path())


def load_rules(path=PATH):
    if not Path(path).exists():
        raise FileNotFoundError(f'Watchlist absente: {path}; montez le fichier YAML dans le conteneur')
    cfg = load_section('watchlists', path)
    for kind in ('whitelist', 'blacklist'):
        for rule in cfg.get(kind, []):
            if not rule.get('name') or not rule.get('keywords'):
                raise ValueError(f'{kind}: chaque règle exige name et keywords')
            if kind == 'whitelist' and rule.get('priority', 'normal') not in ('normal', 'high'):
                raise ValueError(f"Priorité invalide pour {rule['name']}: utilisez normal ou high")
            for pattern in rule['keywords']:
                if rule.get('mode', 'literal') == 'regex':
                    re.compile(pattern, re.I)
    return cfg


def matches(rule, title, summary, content=''):
    scope = rule.get('scope', 'title_summary')
    haystack = title or '' if scope == 'title' else ' '.join([title or '', summary or ''])
    if scope == 'all':
        haystack += ' ' + (content or '')
    if rule.get('mode', 'literal') == 'regex':
        checks = [bool(re.search(p, haystack, re.I)) for p in rule['keywords']]
    else:
        checks = [p.casefold() in haystack.casefold() for p in rule['keywords']]
    return all(checks) if rule.get('operator', 'any') == 'all' else any(checks)


def evaluate(title, summary, content='', config=None):
    cfg = config if config is not None else load_rules()
    matched = {k: [r['name'] for r in cfg.get(k, []) if matches(r, title, summary, content)] for k in ('whitelist', 'blacklist')}
    # Explicit absolute exclusion wins; normal blacklist cannot hide whitelisted items.
    hard = [r['name'] for r in cfg.get('blacklist', []) if r.get('absolute', False) and matches(r, title, summary, content)]
    matched['whitelist_priority'] = (
        'high' if any(r.get('priority', 'normal') == 'high' and
                      matches(r, title, summary, content)
                      for r in cfg.get('whitelist', []))
        else 'normal' if matched['whitelist'] else 'none'
    )
    matched['absolute_blacklist'] = hard
    return matched
