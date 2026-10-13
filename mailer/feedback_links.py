"""Versioned wire protocol and signed email links, without ML dependencies."""
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

REASONS = ('useful', 'off_topic', 'not_applicable', 'shallow_or_promotional', 'not_new', 'already_covered')
LABELS = {
    'fr': ('Utile', 'Hors de mes sujets', 'Pas applicable chez moi', 'Trop superficiel / promotionnel', 'Pas assez nouveau', 'Déjà traité'),
    'en': ('Useful', 'Off topic', 'Not applicable to me', 'Shallow / promotional', 'Not new enough', 'Already covered'),
}
ID = re.compile(r'^[a-f0-9]{64}$')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def valid_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def sign(payload, secret):
    if len(secret.encode()) < 32:
        raise ValueError('Signing secret must contain at least 32 bytes')
    encoded = base64.urlsafe_b64encode(canonical(payload).encode()).rstrip(b'=').decode()
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + '.' + signature


def verify(token, secret, now=None):
    if not isinstance(token, str) or len(token) > 4096:
        raise ValueError('Invalid token')
    try:
        encoded, signature = token.split('.')
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError('Invalid signature')
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        if sign(payload, secret) != token:
            raise ValueError('Noncanonical token')
        validate_token_payload(payload, now)
        return payload
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid token') from exc


def validate_token_payload(p, now=None):
    required = {'v', 'kind', 'presentation_id', 'reason', 'link_id', 'exp'}
    if not isinstance(p, dict) or not required <= p.keys() or p['v'] != 1 or p['kind'] not in ('link', 'form'):
        raise ValueError('Invalid payload')
    if not valid_id(p['presentation_id']) or not valid_id(p['link_id']) or p['reason'] not in REASONS:
        raise ValueError('Invalid payload fields')
    if type(p['exp']) is not int or p['exp'] < (time.time() if now is None else now):
        raise ValueError('Expired token')
    allowed = required if p['kind'] == 'link' else required | {'submission_id', 'submitted_at'}
    if set(p) != allowed:
        raise ValueError('Unexpected fields')
    if p['kind'] == 'form':
        if not valid_id(p['submission_id']):
            raise ValueError('Invalid submission')
        parse_time(p['submitted_at'])


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value):
        raise ValueError('Invalid UTC time')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def validate_return(row):
    keys = {'schema_version', 'id', 'submission_id', 'presentation_id', 'reason', 'received_at', 'submitted_at'}
    if not isinstance(row, dict) or set(row) != keys or type(row['schema_version']) is not int or row['schema_version'] != 1:
        raise ValueError('Invalid return schema')
    if not all(valid_id(row[k]) for k in ('id', 'submission_id', 'presentation_id')) or row['reason'] not in REASONS:
        raise ValueError('Invalid return fields')
    if row['id'] != row['submission_id']:
        raise ValueError('Return identity differs from submission')
    received = parse_time(row['received_at'])
    submitted = parse_time(row['submitted_at'])
    if submitted > received or received > datetime.now(timezone.utc):
        raise ValueError('Invalid return chronology')
    return row


def endpoint(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError('Feedback endpoint must be a plain HTTPS URL')
    return url


def links(presentation_id, url, secret, expires, language='fr'):
    endpoint(url)
    if not valid_id(presentation_id):
        raise ValueError('Invalid presentation ID')
    result = []
    for reason, label in zip(REASONS, LABELS[language]):
        payload = dict(v=1, kind='link', presentation_id=presentation_id, reason=reason,
                       link_id=digest([presentation_id, reason, expires]), exp=expires)
        result.append((label, url + '?' + urlencode({'action': 'vote', 'token': sign(payload, secret)})))
    return result


MARKER = re.compile(r'<!-- feedback:([a-f0-9]{64}) -->')


def briefing_entries(text):
    """Return exact published blocks and the identity used by email links."""
    match = re.search(r'^# .*?\b(\d{4}-\d{2}-\d{2})\b', text, re.MULTILINE)
    if not match:
        raise ValueError('Date du briefing absente')
    day = match[1]
    datetime.fromisoformat(day)  # Reject invalid calendar dates.
    blocks = re.split(r'(?=^### \d+\. )|(?=^## )', text, flags=re.MULTILINE)
    entries = []
    for index, block in enumerate(blocks):
        if not re.match(r'^### \d+\. ', block):
            continue
        urls = sorted(set(re.findall(r'^- \[.*?\]\((https?://[^\s]+)\)$', block, re.MULTILINE)))
        if not urls:
            raise ValueError('Sources absentes pour le feedback')
        event = digest({'scope': 'mail-feedback-v1', 'sources': urls})
        markers = MARKER.findall(block)
        if len(markers) > 1:
            raise ValueError('Marqueurs feedback ambigus')
        pid = markers[0] if markers else digest({'scope': 'mail-feedback-v1', 'event': event, 'briefing_date': day})
        entries.append(dict(index=index, presentation_id=pid, event_id=event,
                            source_urls=urls, content=block, title=block.splitlines()[0]))
    if len({e['presentation_id'] for e in entries}) != len(entries):
        raise ValueError('Présentations feedback en double')
    return day, blocks, entries
