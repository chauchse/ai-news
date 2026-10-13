"""Render configured model instructions without interpreting supplied article text."""
from string import Formatter

# Template fields are the interface between article data and configurable prose.
PROMPT_FIELDS = {
    'filter_previous_briefings_system': set(),
    'deduplicate_security_events_system': set(),
    'deduplicate_security_events_user': {'title_a', 'summary_a', 'title_b', 'summary_b'},
    'business_assessments_system': set(),
    'business_assessments_user': {'title', 'summary', 'priority', 'exploitation',
                                  'cvss', 'applicability', 'products', 'whitelist'},
    'ensure_english_system': set(),
    'summarize_group_system': set(),
    'summarize_group_source': {'index', 'title', 'summary', 'priority', 'cves'},
}


def template(cfg, name):
    prompts = cfg.get('prompts')
    if not isinstance(prompts, dict):
        raise ValueError('briefing.prompts must be a mapping')
    value = prompts.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'briefing.prompts.{name} must be a non-empty string')
    return value


def validate_prompts(cfg):
    for name, allowed in PROMPT_FIELDS.items():
        value = template(cfg, name)
        try:
            fields = set()
            for _, field, spec, conversion in Formatter().parse(value):
                if field is None:
                    continue
                if field not in allowed or spec or conversion:
                    raise ValueError(f'unsupported placeholder: {field}')
                fields.add(field)
            if fields != allowed:
                raise ValueError(f'missing placeholders: {sorted(allowed - fields)}')
        except ValueError as exc:
            raise ValueError(f'briefing.prompts.{name}: {exc}') from exc


def render_prompt(cfg, name, **values):
    return template(cfg, name).format(**values)
