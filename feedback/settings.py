import os
from pathlib import Path
import yaml


def load(path=None):
    path = path or os.getenv('FEEDBACK_CONFIG', '/config/feedback.yaml')
    if not Path(path).is_file():
        return {'enabled': False}
    value = yaml.safe_load(Path(path).read_text())
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('Invalid feedback configuration')
    value['endpoint'] = os.getenv('FEEDBACK_URL', '').strip()
    p = value.get('personalization', {})
    if p.get('mode', 'off') not in ('off', 'shadow', 'active') or p.get('discovery_slots', 0) not in (0, 1):
        raise ValueError('Invalid mode or discovery slots (0 or 1)')
    for key in ('max_examples', 'max_candidates', 'min_training_events', 'min_class_events'):
        if key in p and (type(p[key]) is not int or p[key] < 1):
            raise ValueError('Invalid personalization limit: ' + key)
    for key in ('batch_size', 'max_batches', 'http_timeout', 'max_response_bytes', 'link_days'):
        if key in value and (type(value[key]) is not int or value[key] <= 0):
            raise ValueError('Invalid feedback limit: ' + key)
    if value.get('batch_size', 500) > 500:
        raise ValueError('Public batch maximum is 500')
    return value
