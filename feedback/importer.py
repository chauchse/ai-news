"""Pull, commit locally, then acknowledge exact IDs. No remote purge before commit."""
import json
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mailer.feedback_links import canonical, endpoint, validate_return, NEWSLETTER_REASONS
from mailer.feedback_links import briefing_entries, digest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Feedback redirects are not allowed')


class Client:
    def __init__(self, cfg, secret):
        self.url = endpoint(cfg['endpoint'])
        if len(secret.encode()) < 32:
            raise ValueError('API secret missing or too short')
        self.secret, self.cfg = secret, cfg
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, action, payload=None):
        suffix = '&limit=' + str(self.cfg['batch_size']) if action == 'pull' else ''
        req = urllib.request.Request(self.url + '?action=' + action + suffix,
            data=None if payload is None else canonical(payload).encode(),
            headers={'Authorization': 'Bearer ' + self.secret, 'Content-Type': 'application/json'})
        with self.opener.open(req, timeout=self.cfg['http_timeout']) as response:
            limit = self.cfg['max_response_bytes']
            body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Feedback response too large')
        value = json.loads(body)
        if not isinstance(value, dict) or value.get('schema_version') != 1:
            raise ValueError('Invalid response version')
        return value


def same_submission(a, b):
    # received_at may change if an old POST is replayed after the public purge.
    return all(a.get(k) == b.get(k) for k in ('id','schema_version','submission_id','presentation_id','reason','submitted_at','briefing_date'))


class Store:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def lock(self):
        with self.conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(74110321)')
            if not cur.fetchone()[0]:
                raise RuntimeError('Feedback import already running')
        self.conn.commit()
        try:
            yield
        finally:
            self.conn.rollback()
            with self.conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(74110321)')
            self.conn.commit()

    def save(self, rows):
        from psycopg.types.json import Jsonb
        accepted, inserted = [], 0
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                for row in rows:
                    newsletter = row['reason'] in NEWSLETTER_REASONS
                    table = 'feedback_newsletter_returns' if newsletter else 'feedback_returns'
                    cur.execute(f'SELECT payload FROM {table} WHERE id=%s OR submission_id=%s',
                                (row['id'], row['submission_id']))
                    old = cur.fetchone()
                    if old:
                        if not same_submission(old[0], row):
                            continue # Preserve conflicting public record; do not acknowledge it.
                    else:
                        if newsletter:
                            cur.execute('''INSERT INTO feedback_newsletter_returns
                                (id,submission_id,presentation_id,briefing_date,reason,submitted_at,received_at,payload)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                                (row['id'],row['submission_id'],row['presentation_id'],row['briefing_date'],row['reason'],
                                 row['submitted_at'],row['received_at'],Jsonb(row)))
                        else:
                            cur.execute('''INSERT INTO feedback_returns
                                (id,submission_id,presentation_id,reason,submitted_at,received_at,payload)
                                VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                                (row['id'],row['submission_id'],row['presentation_id'],row['reason'],
                                 row['submitted_at'],row['received_at'],Jsonb(row)))
                        inserted += 1
                    accepted.append(row['id'])
        # Transaction has committed before IDs leave this method.
        return accepted, inserted

    def infer_useful(self, options):
        """Record deductions separately; explicit votes always remain authoritative."""
        from psycopg.types.json import Jsonb
        today = datetime.now(ZoneInfo(options['timezone'])).date()
        day = (today - timedelta(days=1)).isoformat()
        directory = Path(options['daily_dir']) / day
        languages = options['languages']
        if not languages or len(set(languages)) != len(languages) or any(
                language not in ('en', 'fr') for language in languages):
            raise ValueError('Invalid briefing languages')
        if not (directory / 'generated').is_file():
            return 0
        identities = None
        for language in languages:
            edition = directory / language
            if not (edition / 'mail.done').is_file():
                return 0  # Do not infer feedback for an edition not sent yet.
            parsed_day, _, entries = briefing_entries((edition / 'briefing.md').read_text(encoding='utf-8'))
            current = {entry['presentation_id'] for entry in entries}
            if parsed_day != day or not current or (identities is not None and identities != current):
                raise ValueError('Published feedback identities differ')
            identities = current
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute("""SELECT 1 FROM feedback_runs
                    WHERE kind='implicit_useful' AND report->>'briefing_date'=%s""", (day,))
                if cur.fetchone():
                    return 0
                cur.execute('SELECT presentation_id FROM feedback_returns WHERE presentation_id = ANY(%s)',
                            (sorted(identities),))
                explicit = {row[0] for row in cur.fetchall()}
                cur.execute('SELECT EXISTS (SELECT 1 FROM feedback_newsletter_returns WHERE briefing_date=%s)', (day,))
                if not explicit and not cur.fetchone()[0]:
                    return 0
                items = []
                for pid in sorted(identities - explicit):
                    identity = digest({'scope': 'implicit-useful-v1', 'presentation_id': pid})
                    items.append(dict(id=identity, submission_id=identity, presentation_id=pid,
                                      reason='useful', inferred=True, briefing_date=day))
                if items:
                    cur.execute("INSERT INTO feedback_runs(kind,report) VALUES ('implicit_useful',%s)",
                                (Jsonb(dict(briefing_date=day, items=items)),))
                return len(items)


def synchronize(client, store, cfg):
    stats = dict(received=0, imported=0, known=0, invalid=0, public_invalid=0, conflicts=0, confirmed=0, ack_errors=0, batches=0)
    seen = set()
    drained = False
    with store.lock():
        for _ in range(cfg['max_batches']):
            value = client.request('pull')
            rows = value.get('returns')
            if not isinstance(rows, list) or len(rows) > cfg['batch_size']:
                raise ValueError('Invalid pull batch')
            invalid_count = value.get('invalid_count', 0)
            if type(invalid_count) is not int or invalid_count < 0:
                raise ValueError('Invalid public error count')
            stats['public_invalid'] = max(stats['public_invalid'],invalid_count)
            if not rows:
                drained = True
            valid = []
            for row in rows:
                stats['received'] += 1
                try:
                    validate_return(row)
                    if row['id'] not in seen:
                        valid.append(row)
                except (ValueError, TypeError):
                    stats['invalid'] += 1
            if not valid:
                break
            ids, inserted = store.save(valid)
            stats['imported'] += inserted
            stats['known'] += len(ids) - inserted
            stats['conflicts'] += len(valid)-len(ids)
            seen.update(row['id'] for row in valid)
            if not ids:
                break
            result = client.request('ack', {'ids': ids})
            fields = [result.get(key) for key in ('deleted','absent','errors')]
            if any(not isinstance(field, list) for field in fields):
                raise ValueError('Invalid acknowledgment')
            all_ids = [id_ for field in fields for id_ in field]
            if len(all_ids) != len(set(all_ids)) or set(all_ids) != set(ids):
                raise ValueError('Acknowledgment does not match committed IDs')
            stats['confirmed'] += len(fields[0]) + len(fields[1])
            stats['ack_errors'] += len(fields[2])
            stats['batches'] += 1
            seen.update(ids)
            if fields[2]:
                break
        inference = cfg.get('implicit_useful', {})
        if inference.get('enabled', False):
            # A quota stop or anomalous batch must not turn pending votes into approval.
            complete = drained and not any(stats[key] for key in
                ('invalid', 'public_invalid', 'conflicts', 'ack_errors'))
            stats['inferred_useful'] = store.infer_useful(inference) if complete else 0
            stats['inference_deferred'] = int(not complete)
    return stats


def main():
    """Run the existing synchronization from the healthcheck's collector container."""
    import argparse
    import os
    from project_config import load_section

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Configuration centrale')
    parser.add_argument('--dry-run', action='store_true', help='Valider sans réseau ni connexion PostgreSQL')
    args = parser.parse_args()
    stage = 'configuration'
    try:
        cfg = load_section('healthcheck', args.config).get('feedback_import', {})
        if not cfg.get('enabled', False):
            print('Import feedback désactivé')
            return
        cfg = dict(cfg, endpoint=os.environ[cfg['endpoint_env']].strip())
        if cfg.get('implicit_useful', {}).get('enabled', False):
            health = load_section('healthcheck', args.config)
            briefing = load_section('briefing', args.config)['briefing']
            cfg['implicit_useful'] = dict(cfg['implicit_useful'], daily_dir=health['daily_dir'],
                timezone=health['timezone'], languages=briefing.get('languages', [briefing['language']]))
        secret = os.environ[cfg['api_secret_env']]
        client = Client(cfg, secret)
        if secret == os.environ.get('FEEDBACK_SIGNING_SECRET'):
            raise ValueError('API and signing secrets must differ')
        if args.dry_run:
            print('Configuration import feedback valide ; aucun réseau, aucune écriture ni acquittement')
            return
        import psycopg
        stage = 'connexion PostgreSQL'
        with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=cfg['connect_timeout']) as conn:
            stage = 'synchronisation feedback'
            stats = synchronize(client, Store(conn), cfg)
        print('Import feedback : ' + json.dumps(stats, sort_keys=True))
        if any(stats[key] for key in ('invalid', 'public_invalid', 'conflicts', 'ack_errors')):
            parser.exit(1, 'Import feedback incomplet : consulter les compteurs, nouvelle tentative au prochain contrôle.\n')
    except Exception as exc:
        # No exception text: HTTP endpoints, credentials and payloads are private.
        parser.exit(1, f'Échec import feedback — {stage} ({type(exc).__name__}).\n')


if __name__ == '__main__':
    main()

