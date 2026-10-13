"""Convert a Markdown briefing to an HTML email and send it over SMTP."""
import argparse
import os
import re
import time
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

import bleach
import markdown
import yaml
from project_config import load_section
from mailer.feedback_links import MARKER, briefing_entries, links


def address(value):
    if not isinstance(value, str) or '\r' in value or '\n' in value:
        raise ValueError('Adresse mail invalide')
    name, mailbox = parseaddr(value)
    if not mailbox or '@' not in mailbox or any(c.isspace() for c in mailbox):
        raise ValueError('Adresse mail invalide')
    return value


def load_config(path=None):
    cfg = load_section('mail', path)
    if not isinstance(cfg, dict) or not isinstance(cfg.get('smtp'), dict):
        raise ValueError('Configuration mail invalide : section smtp attendue')
    for key in ('sender', 'recipient'):
        cfg[key] = os.environ.get(key.upper()) or cfg.get(key)
        address(cfg[key])
    smtp = cfg['smtp']
    if not isinstance(smtp.get('host'), str) or not smtp['host'].strip():
        raise ValueError('smtp.host obligatoire')
    if smtp.get('security') not in ('starttls', 'ssl', 'none'):
        raise ValueError('smtp.security doit valoir starttls, ssl ou none')
    if not 1 <= int(smtp.get('port', 587)) <= 65535:
        raise ValueError('Port SMTP invalide')
    if float(smtp.get('timeout', 30)) <= 0:
        raise ValueError('Délai SMTP invalide')
    if not isinstance(smtp.get('auth', True), bool):
        raise ValueError('smtp.auth doit être un booléen')
    if smtp.get('auth', True) and smtp['security'] == 'none':
        raise ValueError('Authentification SMTP : TLS requis')
    return cfg


def render_html(text, language="fr"):
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    body = bleach.clean(
        body,
        tags=['p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong',
              'em', 'ul', 'ol', 'li', 'a', 'blockquote', 'pre', 'code',
              'table', 'thead', 'tbody', 'tr', 'th', 'td'],
        attributes={'a': ['href', 'title']}, protocols=['https', 'http', 'mailto'],
        strip=True,
    )
    body = re.sub(r'<a href="([^" ]+[?]action=vote(?:&amp;|&)token=[^" ]+)">',
                  r'<a href="\1" style="display:inline-block;padding:8px 12px;margin:4px 2px;'
                  r'border:1px solid #cbd5e1;border-radius:6px;text-decoration:none;color:#172033">', body)
    return (f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
            '<title>AI News</title></head><body style="margin:0;padding:24px;'
            'background:#f3f4f6;color:#172033;font-family:Arial,sans-serif;line-height:1.6">'
            '<div style="max-width:760px;margin:auto;padding:24px;background:white">'
            + body + '</div></body></html>')


def add_feedback(text, cfg):
    options = cfg.get('feedback', {})
    enabled = options.get('enabled', True)
    if type(enabled) is not bool:
        raise ValueError('mail.feedback.enabled doit être booléen')
    url = os.getenv('FEEDBACK_URL', '').strip()
    if not enabled or not url:
        return MARKER.sub('', text)
    days = options.get('link_days', 90)
    if type(days) is not int or days < 1:
        raise ValueError('Durée des liens invalide')
    day, blocks, entries = briefing_entries(text)
    # Direct-vote PHP recovers submitted_at as exp - link_seconds.
    # Use an exact duration: calendar arithmetic across DST shifts that date.
    issued = int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Europe/Paris')).timestamp())
    now = time.time()
    if issued > now:
        raise ValueError('Date du briefing future')
    expires = issued + days * 86400
    if expires <= now:
        raise ValueError('Liens feedback expirés')
    language = cfg.get('language', 'fr')
    for entry in entries:
        index, pid = entry['index'], entry['presentation_id']
        block = blocks[index]
        values = links(pid, url, os.getenv('FEEDBACK_SIGNING_SECRET', ''), expires, language)
        label = 'Votre avis' if language == 'fr' else 'Your feedback'
        buttons = '**' + label + '** : ' + ' · '.join('[' + name + '](' + link + ')' for name, link in values)
        blocks[index] = MARKER.sub('', block).rstrip() + '\n\n' + buttons + '\n\n'
    return ''.join(blocks)


def build_message(cfg):
    text = Path(cfg.get('input', '/output/briefing.md')).read_text(encoding='utf-8')
    if not text.strip():
        raise ValueError('Briefing Markdown vide')
    text = add_feedback(text, cfg)
    today = datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()
    subject = str(cfg.get('subject', 'AI News — Briefing du {date}')).replace('{date}', today)
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = address(os.environ.get('SENDER') or cfg['sender'])
    msg['To'] = address(os.environ.get('RECIPIENT') or cfg['recipient'])
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()
    msg.set_content(text)
    msg.add_alternative(render_html(text, cfg.get("language", "fr")), subtype='html')
    return msg


def send_message(cfg, message):
    smtp = cfg['smtp']
    username = os.environ.get('SMTP_USERNAME')
    password = os.environ.get('SMTP_PASSWORD')
    if smtp.get('auth', True) and (not username or not password):
        raise ValueError('SMTP_USERNAME et SMTP_PASSWORD obligatoires dans .env')
    context = ssl.create_default_context()
    options = {'host': smtp['host'], 'port': int(smtp.get('port', 587)),
               'timeout': float(smtp.get('timeout', 30))}
    client = smtplib.SMTP_SSL if smtp['security'] == 'ssl' else smtplib.SMTP
    if smtp['security'] == 'ssl':
        options['context'] = context
    with client(**options) as server:
        server.ehlo()
        if smtp['security'] == 'starttls':
            server.starttls(context=context)
            server.ehlo()
        if smtp.get('auth', True):
            server.login(username, password)
        refused = server.send_message(message)
        if refused:
            raise RuntimeError('Destinataire refusé par le serveur SMTP')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Configuration globale ; défaut : config.yaml / AI_NEWS_CONFIG')
    parser.add_argument('--dry-run', action='store_true', help='Créer un aperçu HTML sans connexion SMTP')
    parser.add_argument('--preview', help='Chemin HTML personnalisé ; sinon briefing-email-<langue>.html')
    parser.add_argument('--language', choices=['en', 'fr'])
    parser.add_argument('--input')
    args = parser.parse_args()
    stage = "configuration (YAML, SENDER, RECIPIENT)"
    try:
        cfg = load_config(args.config)
        if args.language:
            cfg.update(cfg.get('editions', {}).get(args.language, {}))
            cfg['language'] = args.language
            cfg.setdefault('subject', 'AI News — {date}')
            cfg['subject'] = f"[{args.language.upper()}] " + cfg['subject']
        if args.input:
            cfg['input'] = args.input
        stage = "lecture du Markdown et préparation du message"
        message = build_message(cfg)
        stage = "écriture de l’aperçu HTML"
        language_suffix = f"-{args.language}" if args.language else ""
        default_preview = (Path(args.input).with_name('briefing-email.html') if args.input
                           else Path(f"/output/briefing-email{language_suffix}.html"))
        output = Path(args.preview) if args.preview else default_preview
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(message.get_body(preferencelist=('html',)).get_content(), encoding='utf-8')
        if args.dry_run:
            print(f'Aperçu HTML : {output} ; aucun mail envoyé')
        else:
            stage = "connexion, authentification ou envoi SMTP"
            send_message(cfg, message)
            print('Mail accepté par le serveur SMTP')
    except (OSError, ValueError, smtplib.SMTPException, yaml.YAMLError, RuntimeError) as exc:
        # Only report the stage/type/code, never SMTP responses or credentials.
        detail = type(exc).__name__
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            detail += " : authentification refusée"
        if isinstance(exc, smtplib.SMTPResponseException):
            detail += f" (code SMTP {exc.smtp_code})"
        parser.exit(1, f"Échec à l’étape {stage} — {detail}.\n")



if __name__ == '__main__':
    main()
