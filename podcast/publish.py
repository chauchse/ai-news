#!/usr/bin/env python3
"""Publish a rolling seven-day AI News podcast to an OVH SFTP site."""
import argparse
import datetime as dt
import email.utils
import html
import hashlib
import json
import logging
import os
import posixpath
import re
import stat
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin

import paramiko
import yaml

LOG = logging.getLogger('podcast')
ITUNES = 'http://www.itunes.com/dtds/podcast-1.0.dtd'
ATOM = 'http://www.w3.org/2005/Atom'
ET.register_namespace('itunes', ITUNES)
ET.register_namespace('atom', ATOM)
DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})(?:-[a-f0-9]{64})?-briefing\.mp3$')
ARTICLE_RE = re.compile(r'^#{2,3}\s+(\d+)\.\s+(.+?)\s*$')
SOURCE_RE = re.compile(r'^\s*(?:[-*]\s*)?(?:\*\*)?Sources?\s*(?:\*\*)?\s*:\s*(.*)$', re.I)
MD_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)(?:\s+[^)]*)?\)')


def config_load(path):
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    if not isinstance(data, dict):
        raise ValueError('Podcast config must be a YAML mapping')
    return data


def clean_markdown(value):
    value = MD_LINK_RE.sub(lambda m: m.group(1), value)
    value = re.sub(r'\*\*|__|`', '', value)
    return value.strip()


def parse_articles(markdown):
    articles = []
    current = None
    for raw in markdown.splitlines():
        line = raw.strip()
        match = ARTICLE_RE.match(line)
        if match:
            current = {'title': clean_markdown(match.group(2)), 'sources': []}
            articles.append(current)
            continue
        if re.match(r'^#{2,3}\s+', line) and re.search(r'annexe|non retenu|autres alertes', line, re.I):
            break
        if current is None:
            continue
        source = SOURCE_RE.match(line)
        if source:
            source_text = source.group(1).strip()
            links = MD_LINK_RE.findall(source_text)
            if links:
                for label, url in links:
                    current['sources'].append({'name': clean_markdown(label), 'url': url})
            else:
                for part in re.split(r'\s*[;,]\s*', source_text):
                    if part:
                        current['sources'].append({'name': clean_markdown(part), 'url': None})
            current['_sources_block'] = not bool(source_text)
            continue
        # Common alternative: bullet links immediately following "Sources:"
        if re.match(r'^[-*]\s+', line) and current.get('_sources_block'):
            links = MD_LINK_RE.findall(line)
            for label, url in links:
                current['sources'].append({'name': clean_markdown(label), 'url': url})
            if not links:
                label = clean_markdown(re.sub(r'^[-*]\s+', '', line))
                if label:
                    current['sources'].append({'name': label, 'url': None})
        elif line and not line.startswith(('-', '*')):
            current['_sources_block'] = bool(re.match(r'^Sources?\s*:?$', line, re.I))
    for article in articles:
        article.pop('_sources_block', None)
    return articles


def episode_description(articles):
    lines = ['Au sommaire de ce briefing :', '']
    for i, article in enumerate(articles, 1):
        lines.append(f"{i}. {article['title']}")
        if article['sources']:
            for source in article['sources']:
                label = source['name']
                lines.append(f"   Source : {label}" + (f" — {source['url']}" if source.get('url') else ''))
        else:
            lines.append('   Source : non précisée')
        lines.append('')
    return '\n'.join(lines).strip()


def get_audio_duration(path):
    # Optional: use ffprobe if installed, otherwise RSS duration is omitted.
    import subprocess
    try:
        p = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                           check=True, capture_output=True, text=True)
        seconds = round(float(p.stdout.strip()))
        return f'{seconds // 3600:02}:{(seconds // 60) % 60:02}:{seconds % 60:02}'
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def append_episode(state, episode_file, articles, now, retention):
    """Keep distinct audio files, and make retries of the same file idempotent."""
    cutoff = now.date() - dt.timedelta(days=retention - 1)
    state = [ep for ep in state if dt.date.fromisoformat(ep['date']) >= cutoff]
    digest = hashlib.sha256()
    with episode_file.open('rb') as audio:
        for chunk in iter(lambda: audio.read(1024 * 1024), b''):
            digest.update(chunk)
    filename = f'{now.date().isoformat()}-{digest.hexdigest()}-briefing.mp3'
    existing = next((ep for ep in state if ep['filename'] == filename), None)
    if existing:
        return state, existing
    episode = {
        'date': now.date().isoformat(),
        'filename': filename,
        'description': episode_description(articles),
        'bytes': episode_file.stat().st_size,
        'duration': get_audio_duration(episode_file),
        'published_at': now.isoformat(),
    }
    state.append(episode)
    state.sort(key=lambda ep: ep['published_at'], reverse=True)
    return state, episode


def write_feed(config, episodes, destination):
    base = config['site_url'].rstrip('/') + '/'
    feed_url = urljoin(base, 'podcast.xml')
    channel_info = config['podcast']
    rss = ET.Element('rss', {'version': '2.0'})
    channel = ET.SubElement(rss, 'channel')
    def elem(parent, name, value):
        ET.SubElement(parent, name).text = str(value)
    elem(channel, 'title', channel_info['title'])
    elem(channel, 'link', base)
    elem(channel, 'description', channel_info['description'])
    elem(channel, 'language', channel_info.get('language', 'fr-FR'))
    elem(channel, f'{{{ITUNES}}}author', channel_info['author'])
    elem(channel, f'{{{ITUNES}}}summary', channel_info['description'])
    elem(channel, f'{{{ITUNES}}}explicit', 'false')
    elem(channel, f'{{{ITUNES}}}type', 'episodic')
    ET.SubElement(channel, f'{{{ITUNES}}}image', {'href': urljoin(base, config['cover'])})
    owner = ET.SubElement(channel, f'{{{ITUNES}}}owner')
    elem(owner, f'{{{ITUNES}}}name', channel_info['author'])
    elem(owner, f'{{{ITUNES}}}email', channel_info['email'])
    ET.SubElement(channel, f'{{{ITUNES}}}category', {'text': channel_info.get('category', 'Technology')})
    ET.SubElement(channel, f'{{{ATOM}}}link', {'href': feed_url, 'rel': 'self', 'type': 'application/rss+xml'})
    image = ET.SubElement(channel, 'image')
    elem(image, 'url', urljoin(base, config['cover']))
    elem(image, 'title', channel_info['title'])
    elem(image, 'link', base)

    for ep in sorted(episodes, key=lambda e: e['published_at'], reverse=True):
        item = ET.SubElement(channel, 'item')
        title = f"Briefing du {dt.date.fromisoformat(ep['date']).strftime('%d/%m/%Y')}"
        elem(item, 'title', title)
        elem(item, 'description', ep['description'])
        elem(item, f'{{{ITUNES}}}summary', ep['description'])
        elem(item, f'{{{ITUNES}}}explicit', 'false')
        mp3_url = urljoin(base, 'episodes/' + ep['filename'])
        guid = ET.SubElement(item, 'guid', {'isPermaLink': 'false'})
        guid.text = mp3_url
        published = dt.datetime.fromisoformat(ep['published_at'])
        elem(item, 'pubDate', email.utils.format_datetime(published))
        ET.SubElement(item, 'enclosure', {
            'url': mp3_url, 'length': str(ep['bytes']), 'type': 'audio/mpeg'
        })
        if ep.get('duration'):
            elem(item, f'{{{ITUNES}}}duration', ep['duration'])
    tree = ET.ElementTree(rss)
    ET.indent(tree, space='  ')
    tree.write(destination, encoding='utf-8', xml_declaration=True)


def mkdir_p(sftp, path):
    path = posixpath.normpath(path)
    if not path.startswith('/'):
        raise ValueError('remote_path must be an absolute SFTP path')
    cursor = '/'
    for part in path.split('/'):
        if not part:
            continue
        cursor = posixpath.join(cursor, part)
        try:
            attrs = sftp.stat(cursor)
            if not stat.S_ISDIR(attrs.st_mode):
                raise NotADirectoryError(cursor)
        except FileNotFoundError:
            sftp.mkdir(cursor)


def atomic_upload(sftp, local, remote):
    temp = remote + '.uploading'
    try:
        sftp.put(str(local), temp)
        sftp.posix_rename(temp, remote)
    except Exception:
        try:
            sftp.remove(temp)
        except OSError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/config/podcast.yaml')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = config_load(args.config)
    retention = int(cfg.get('retention_days', 7))
    if retention < 1:
        raise ValueError('retention_days must be >= 1')
    # Use Europe/Paris including daylight-saving transitions.
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo('Europe/Paris'))
    cutoff = now.date() - dt.timedelta(days=retention - 1)
    output_dir = Path(cfg.get('output_dir', '/output'))
    state_file = Path(cfg.get('state_file', '/state/episodes.json'))
    markdown_file = output_dir / cfg.get('briefing_file', 'briefing.md')
    episode_file = output_dir / f'{now.date().isoformat()}-briefing.mp3'
    if not episode_file.is_file():
        raise FileNotFoundError(f'MP3 absent : {episode_file}')
    if not markdown_file.is_file():
        raise FileNotFoundError(f'Briefing absent : {markdown_file}')
    articles = parse_articles(markdown_file.read_text(encoding='utf-8'))
    if not articles:
        raise ValueError('Aucun article reconnu dans briefing.md')
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding='utf-8'))
        if not isinstance(state, list):
            raise ValueError('Invalid episode state')
    else:
        state = []
    state, new_ep = append_episode(state, episode_file, articles, now, retention)
    cover = Path(cfg.get('cover_file', '/podcast/cover.jpg'))
    if not cover.is_file():
        raise FileNotFoundError(f'Illustration absente : {cover}')
    if not cfg['cover'].lower().endswith(('.jpg', '.jpeg', '.png')):
        raise ValueError('cover must be a JPG or PNG image filename')
    remote_root = cfg['sftp']['remote_path'].rstrip('/')
    if remote_root in ('', '/'):
        raise ValueError('Refusing to publish into SFTP root')
    host = os.environ.get('PODCAST_SFTP_HOST', cfg['sftp'].get('host', ''))
    port = int(os.environ.get('PODCAST_SFTP_PORT', cfg['sftp'].get('port', 22)))
    username = os.environ.get('PODCAST_SFTP_USER')
    password = os.environ.get('PODCAST_SFTP_PASSWORD')
    key_file = os.environ.get('PODCAST_SFTP_KEY_FILE')
    if args.dry_run:
        LOG.info('DRY RUN: %s -> %s:%s', episode_file, host, remote_root)
        LOG.info('Articles: %d; retained episodes: %d; cutoff: %s', len(articles), len(state), cutoff)
        LOG.info('Description:\n%s', new_ep['description'])
        return
    if not username or not (password or key_file):
        raise ValueError('PODCAST_SFTP_USER and PODCAST_SFTP_PASSWORD or PODCAST_SFTP_KEY_FILE required')

    state_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        feed_file = Path(td) / 'podcast.xml'
        write_feed(cfg, state, feed_file)
        ssh = paramiko.SSHClient()
        ssh.load_system_host_keys()
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            ssh.connect(hostname=host, port=port, username=username, password=password,
                        key_filename=key_file, timeout=20, auth_timeout=20, look_for_keys=False,
                        allow_agent=False)
            sftp = ssh.open_sftp()
            try:
                episodes_remote = posixpath.join(remote_root, 'episodes')
                mkdir_p(sftp, episodes_remote)
                atomic_upload(sftp, episode_file, posixpath.join(episodes_remote, new_ep['filename']))
                atomic_upload(sftp, cover, posixpath.join(remote_root, cfg['cover']))
                # Feed published only after audio and artwork are available.
                atomic_upload(sftp, feed_file, posixpath.join(remote_root, 'podcast.xml'))
                # Only delete specifically named old briefing MP3 files.
                for attr in sftp.listdir_attr(episodes_remote):
                    match = DATE_RE.fullmatch(attr.filename)
                    if not match or not stat.S_ISREG(attr.st_mode):
                        continue
                    try:
                        date = dt.date.fromisoformat(match.group(1))
                    except ValueError:
                        continue
                    if date < cutoff:
                        sftp.remove(posixpath.join(episodes_remote, attr.filename))
                        LOG.info('Deleted expired episode: %s', attr.filename)
                tmp_state = state_file.with_suffix('.json.tmp')
                tmp_state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp_state.replace(state_file)
                LOG.info('Published %s (%d articles), RSS has %d episodes', new_ep['filename'], len(articles), len(state))
            finally:
                sftp.close()
        finally:
            ssh.close()


if __name__ == '__main__':
    main()
