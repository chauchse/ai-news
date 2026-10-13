"""Inspect daily completion markers; send an alert only with --send."""
import argparse
import os
import re
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from project_config import load_section
from mailer.send import address, load_config, send_message


def missing_steps(root, day, languages):
    directory = Path(root) / day.isoformat()
    missing = []
    if not (directory / 'generated').is_file():
        missing.append('Génération bilingue : marqueur generated absent')
    for language in languages:
        edition = directory / language
        stages = (
            ('Markdown', None, 'briefing.md'),
            ('mail', 'mail.done', 'briefing-email.html'),
            ('audio', 'audio.done', f'{day.isoformat()}-briefing.mp3'),
            ('podcast', 'podcast.done', 'podcast.xml'),
        )
        for label, marker, artifact in stages:
            absent = []
            if marker and not (edition / marker).is_file():
                absent.append(f'{marker} absent')
            path = edition / artifact
            if not path.is_file() or path.stat().st_size == 0:
                absent.append(f'{artifact} absent ou vide')
            if absent:
                missing.append(f'{language.upper()} — {label} : ' + ', '.join(absent))
    return missing


def log_excerpt(options):
    # Read only a bounded tail. Rotated logs and older runs may be outside it.
    try:
        with Path(options['log_path']).open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            offset = max(0, size - options['log_max_bytes'])
            stream.seek(offset)
            data = stream.read(options['log_max_bytes'])
        if offset:
            data = data.partition(b'\n')[2]
        text = '\n'.join(data.decode('utf-8', errors='replace').splitlines()
                         [-options['log_tail_lines']:])
    except OSError:
        return 'Journal inaccessible : vérifier le fichier et son montage en lecture seule.\n'
    # Signed feedback links and credentials must never reach the attachment.
    for key, value in os.environ.items():
        if value and any(word in key.upper() for word in ('PASSWORD', 'SECRET', 'TOKEN', 'KEY', 'USERNAME')):
            text = text.replace(value, '[SECRET MASQUÉ]')
    text = re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+', '[URL MASQUÉE]', text)
    text = re.sub(r'(?im)\bauthorization\s*[:=][^\r\n]*', 'Authorization: [MASQUÉ]', text)
    text = re.sub(r'(?i)\b(password|passwd|secret|token|authorization|api_key)\b'
                  r'\s*[:=]\s*[^\s,;]+', r'\1=[MASQUÉ]', text)
    return text + '\n' if text else 'Journal vide ou extrait sans ligne complète.\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Configuration centrale')
    parser.add_argument('--date', type=date.fromisoformat, help='Édition précise, AAAA-MM-JJ')
    parser.add_argument('--repair-plan', action='store_true',
                        help='Afficher date et étapes autorisées pour l’orchestrateur, sans exécution')
    parser.add_argument('--repair-report', help='Compte rendu de reprise à inclure dans l’alerte')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--send', action='store_true', help='Envoyer réellement une alerte SMTP')
    mode.add_argument('--dry-run', action='store_true', help='Diagnostic sans SMTP (défaut)')
    args = parser.parse_args()
    if args.repair_plan and args.send:
        parser.error('--repair-plan est incompatible avec --send')
    stage = 'configuration'
    try:
        options = load_section('healthcheck', args.config)
        briefing = load_section('briefing', args.config)['briefing']
        languages = briefing.get('languages', [briefing['language']])
        if not languages or len(set(languages)) != len(languages) or any(
                language not in ('en', 'fr') for language in languages):
            raise ValueError('Langues de briefing invalides')
        now = datetime.now(ZoneInfo(options['timezone']))
        # Before the deadline, the last expected edition is yesterday's.
        start = now.replace(hour=options['daily_start_hour'], minute=0, second=0, microsecond=0)
        deadline = start.timestamp() + options['grace_hours'] * 3600
        day = args.date or (now.date() if now.timestamp() >= deadline
                            else now.date() - timedelta(days=1))
        if day > now.date():
            raise ValueError('Édition future interdite')
        stage = 'inspection des étapes'
        missing = missing_steps(options['daily_dir'], day, languages)
        if args.repair_plan:
            print(day.isoformat())
            repair = options.get('repair', {})
            if missing and repair.get('enabled', False):
                for step in repair.get('stages', []):
                    print(step)
            return
        if not missing:
            print(f'HEALTHCHECK OK : édition {day}, aucun mail envoyé')
            return
        subject = options['subject'].format(date=day.isoformat(), count=len(missing))
        excerpt = log_excerpt(options)
        print(subject)
        for step in missing:
            print(' - ' + step)
        if not args.send:
            print('DRY RUN : aucun mail envoyé ; aucune sortie écrite')
            return
        stage = 'préparation de l’alerte'
        cfg = load_config(args.config)
        # Require the .env recipient rather than a configuration fallback.
        recipient = address(os.environ.get('RECIPIENT'))
        message = EmailMessage()
        message['Subject'] = subject
        message['From'] = address(cfg['sender'])
        message['To'] = recipient
        message['Date'] = formatdate(localtime=True)
        message['Message-ID'] = make_msgid()
        message.set_content(
            f'Édition attendue : {day}\nContrôle : {now.isoformat()}\n\n'
            + '\n'.join('- ' + step for step in missing)
            + '\n\n' + (args.repair_report or 'Aucune étape relancée.')
            + '\nLes marqueurs attestent les traitements locaux, '
              'pas la réception des mails ni la disponibilité distante du podcast.\n'
              'Pièce jointe : fin du journal, bornée et expurgée ; elle peut inclure '
              'd’autres exécutions ou ne plus contenir la panne recherchée.\n'
              'Une alerte sera répétée au prochain contrôle tant que l’édition reste incomplète.\n')
        message.add_attachment(excerpt.encode('utf-8'), maintype='text', subtype='plain',
                               filename=f'ai-news-healthcheck-{day}.log')
        stage = 'envoi SMTP'
        send_message(cfg, message)
        print('Alerte acceptée par le serveur SMTP')
    except (OSError, ValueError, KeyError, TypeError, smtplib.SMTPException,
            yaml.YAMLError, RuntimeError) as exc:
        # Do not expose SMTP responses, signed links or configuration secrets.
        parser.exit(1, f'Échec healthcheck — {stage} ({type(exc).__name__}).\n')


if __name__ == '__main__':
    main()
