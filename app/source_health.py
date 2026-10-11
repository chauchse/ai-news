"""Persist source retirement while preserving YAML formatting and comments."""
import fcntl
import os
from pathlib import Path
import shutil
import tempfile

from ruamel.yaml import YAML


def consecutive_failures(conn, source_id, threshold):
    with conn.cursor() as cur:
        cur.execute('SELECT status, error_message FROM collection_runs WHERE source_id = %s '
                    'ORDER BY id DESC LIMIT %s', (source_id, threshold))
        statuses = cur.fetchall()
    return len(statuses) == threshold and all(status == 'failed' and (error or '').startswith('[feed] ')
                                                for status, error in statuses)


def disable_source(path, url):
    path = Path(path)
    with path.with_name('.sources.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(path)
        matches = [source for source in data.get('sources', []) if source['url'] == url]
        if not matches:
            raise ValueError('Source missing from current configuration')
        if all(source.get('enabled', True) is False for source in matches):
            return
        for source in matches:
            source['enabled'] = False
        shutil.copy2(path, path.with_suffix(path.suffix + '.bak'))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.sources-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                yaml.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(path.stat().st_mode & 0o777)
            temporary.replace(path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
