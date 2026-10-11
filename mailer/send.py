"""Convert a Markdown briefing to an HTML email and send it over SMTP."""
import argparse
import os
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


def address(value):
    if not isinstance(value, str) or '\r' in value or '\n' in value:
        raise ValueError('Adresse mail invalide')
    name, mailbox = parseaddr(value)
    if not mailbox or '@' not in mailbox or any(c.isspace() for c in mailbox):
        raise ValueError('Adresse mail invalide')
    return value


def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(cfg, dict) or not isinstance(cfg.get('smtp'), dict):
        raise ValueError('Configuration mail invalide : section smtp attendue')
    for key in ('sender', 'recipient'):
        address(cfg.get(key))
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


def render_html(text):
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    body = bleach.clean(
        body,
        tags=['p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong',
              'em', 'ul', 'ol', 'li', 'a', 'blockquote', 'pre', 'code',
              'table', 'thead', 'tbody', 'tr', 'th', 'td'],
        attributes={'a': ['href', 'title']}, protocols=['https', 'http', 'mailto'],
        strip=True,
    )
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<title>AI News</title></head><body style="margin:0;padding:24px;'
            'background:#f3f4f6;color:#172033;font-family:Arial,sans-serif;line-height:1.6">'
            '<div style="max-width:760px;margin:auto;padding:24px;background:white">'
            + body + '</div></body></html>')


def build_message(cfg):
    text = Path(cfg.get('input', '/output/briefing.md')).read_text(encoding='utf-8')
    if not text.strip():
        raise ValueError('Briefing Markdown vide')
    today = datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()
    subject = str(cfg.get('subject', 'AI News — Briefing du {date}')).replace('{date}', today)
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = cfg['sender']
    msg['To'] = cfg['recipient']
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()
    msg.set_content(text)
    msg.add_alternative(render_html(text), subtype='html')
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
    parser.add_argument('--config', default='/config/mail.yaml')
    parser.add_argument('--dry-run', action='store_true', help='Créer un aperçu HTML sans connexion SMTP')
    parser.add_argument('--preview', default='/output/briefing-email.html')
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        message = build_message(cfg)
        if args.dry_run:
            output = Path(args.preview)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(message.get_body(preferencelist=('html',)).get_content(), encoding='utf-8')
            print(f'Aperçu HTML : {output} ; aucun mail envoyé')
        else:
            send_message(cfg, message)
            print('Mail accepté par le serveur SMTP')
    except (OSError, ValueError, smtplib.SMTPException, yaml.YAMLError, RuntimeError):
        # Avoid printing SMTP responses, which may contain credentials or message content.
        parser.exit(1, 'Échec de l’envoi ou de la préparation : vérifier le fichier Markdown, la configuration et l’accès SMTP.\n')


if __name__ == '__main__':
    main()
