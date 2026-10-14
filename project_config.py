"""Shared access to the root application configuration (no secrets)."""
import os
import math
import re
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
        if path is None or source.resolve() == config_path().resolve():
            raise ValueError('Global configuration version missing')
        return data  # Compatibility for existing CLI inputs and test fixtures.
    if type(data['config_version']) is not int or data['config_version'] != 1:
        raise ValueError('Unsupported configuration version')
    value = data.get(name)
    if not isinstance(value, dict):
        raise ValueError('Missing configuration section: ' + name)
    if name == 'glossary':
        terms = value.get('case_sensitive_terms', [])
        if not isinstance(terms, list) or any(not isinstance(term, str) or not term for term in terms):
            raise ValueError('glossary.case_sensitive_terms must be a list of non-empty strings')
    if name == 'healthcheck':
        for key in ('timezone', 'calendar', 'daily_dir', 'log_path', 'subject'):
            if (not isinstance(value.get(key), str) or not value[key].strip()
                    or '\n' in value[key] or '\r' in value[key]):
                raise ValueError('Invalid healthcheck.' + key)
        for key, minimum, maximum in (
                ('daily_start_hour', 0, 23), ('grace_hours', 0, 23),
                ('log_max_bytes', 1, 1048576), ('log_tail_lines', 1, 10000)):
            number = value.get(key)
            if type(number) is not int or not minimum <= number <= maximum:
                raise ValueError('Invalid healthcheck.' + key)
        if value['daily_start_hour'] + value['grace_hours'] >= 24:
            raise ValueError('Healthcheck deadline must precede midnight')
        if any(not Path(value[key]).is_absolute() for key in ('daily_dir', 'log_path')):
            raise ValueError('Healthcheck paths must be absolute')
        value['subject'].format(date='2000-01-01', count=1)
        repair = value.get('repair', {})
        if not isinstance(repair, dict) or type(repair.get('enabled', False)) is not bool:
            raise ValueError('Invalid healthcheck.repair.enabled')
        stages = repair.get('stages', [])
        if (not isinstance(stages, list) or any(
                stage not in ('markdown', 'preview', 'audio', 'mail', 'podcast') for stage in stages)
                or len(set(stages)) != len(stages)):
            raise ValueError('Invalid healthcheck.repair.stages')
        feedback = value.get('feedback_import', {})
        if not isinstance(feedback, dict) or type(feedback.get('enabled', False)) is not bool:
            raise ValueError('Invalid healthcheck.feedback_import.enabled')
        if feedback.get('enabled', False):
            for key in ('batch_size', 'max_batches', 'http_timeout', 'max_response_bytes', 'connect_timeout'):
                number = feedback.get(key)
                if type(number) is not int or number < 1:
                    raise ValueError('Invalid healthcheck.feedback_import.' + key)
            if feedback['batch_size'] > 500:
                raise ValueError('Feedback batch exceeds receiver protocol limit')
            for key in ('endpoint_env', 'api_secret_env'):
                if not isinstance(feedback.get(key), str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', feedback[key]):
                    raise ValueError('Invalid healthcheck.feedback_import.' + key)
    if name in ('briefing', 'semantic_scoring'):
        weights = data.get('scoring', {}).get('domain_weights')
        if not isinstance(weights, dict) or not weights:
            raise ValueError('scoring.domain_weights must be a non-empty mapping')
        for domain, weight in weights.items():
            if (not isinstance(domain, str) or not domain.strip()
                    or isinstance(weight, bool) or not isinstance(weight, (int, float))
                    or not math.isfinite(weight) or weight < 0):
                raise ValueError('Invalid scoring.domain_weights entry: ' + str(domain))
        if name == 'briefing':
            topics = value.get('briefing', {}).get('strategic_topics')
            if not isinstance(topics, dict):
                raise ValueError('briefing.briefing.strategic_topics must be a mapping')
            limit = topics.get('summary_max_chars')
            if type(limit) is not int or limit < 0:
                raise ValueError('strategic_topics.summary_max_chars must be a non-negative integer')
            patterns = topics.get('patterns')
            if not isinstance(patterns, dict):
                raise ValueError('strategic_topics.patterns must be a mapping')
            fallback = topics.get('fallback')
            if not isinstance(fallback, str) or not fallback.strip() or fallback in patterns:
                raise ValueError('strategic_topics.fallback must be non-empty and distinct from pattern names')
            for topic, pattern in patterns.items():
                if not isinstance(topic, str) or not topic.strip() or not isinstance(pattern, str) or not pattern.strip():
                    raise ValueError('Invalid strategic_topics pattern: ' + str(topic))
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError('Invalid strategic_topics regex: ' + topic) from exc
            scoring = value.setdefault('scoring', {})
            if 'domains' in scoring:
                raise ValueError('Use scoring.domain_weights instead of briefing.scoring.domains')
            scoring['domains'] = {domain: {'weight': weight} for domain, weight in weights.items()}
        else:
            for domain in value.get('domains', []):
                if 'weight' in domain:
                    raise ValueError('Use scoring.domain_weights instead of semantic domain weights')
                domain_name = domain.get('name')
                if domain_name not in weights:
                    raise ValueError('Missing domain weight: ' + str(domain_name))
                domain['weight'] = weights[domain_name]
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
