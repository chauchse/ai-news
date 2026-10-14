"""Pull, commit locally, then acknowledge exact IDs. No remote purge before commit."""
import json
import urllib.request
from contextlib import contextmanager

from mailer.feedback_links import canonical, endpoint, validate_return, NEWSLETTER_REASONS


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


def synchronize(client, store, cfg):
    stats = dict(received=0, imported=0, known=0, invalid=0, public_invalid=0, conflicts=0, confirmed=0, ack_errors=0, batches=0)
    seen = set()
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

