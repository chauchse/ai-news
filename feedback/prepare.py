"""Build a private local deployment bundle; never connects or publishes."""
import argparse
import os
from pathlib import Path
from feedback.common import endpoint


def php_string(value):
    return "'" + value.replace('\\','\\\\').replace("'","\\'") + "'"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-dir',required=True,help='Absolute PRIVATE directory on public host, outside document root')
    parser.add_argument('--output',default='output/feedback-deploy')
    args=parser.parse_args()
    if not args.private_dir.startswith('/') or any(c in args.private_dir for c in ('\n','\r','\0')):
        parser.error('Absolute private path required')
    endpoint(os.getenv('FEEDBACK_URL',''))
    signing=os.getenv('FEEDBACK_SIGNING_SECRET',''); api=os.getenv('FEEDBACK_API_SECRET','')
    if len(signing)<32 or len(api)<32 or signing==api:
        parser.error('Distinct signing/API secrets of at least 32 bytes required')
    root=Path(args.output)
    if root.exists():
        parser.error('Output already exists; choose a fresh directory to preserve previous bundle')
    root.mkdir(parents=True,mode=0o700)
    public=root/'public'/'feedback'; public.mkdir(parents=True,mode=0o700)
    private=root/'private';private.mkdir(mode=0o700);(private/'pending').mkdir(mode=0o700)
    web=Path(__file__).resolve().parent.parent/'web'
    source=web/'public'/'feedback.php'
    text=source.read_text().replace("const PRIVATE_CONFIG = '/REPLACE/private/ai-news-feedback/config.php';",
        'const PRIVATE_CONFIG = '+php_string(args.private_dir.rstrip('/')+'/config.php')+';')
    (public/'feedback.php').write_text(text)
    (public/'.htaccess').write_text((web/'public'/'.htaccess').read_text())
    template=(web/'private.example'/'config.php').read_text()
    template=template.replace("'REPLACE_WITH_AT_LEAST_32_RANDOM_BYTES'",php_string(signing)).replace(
        "'REPLACE_WITH_A_DIFFERENT_RANDOM_SECRET'",php_string(api))
    (private/'config.php').write_text(template); (private/'config.php').chmod(0o600)
    (public/'feedback.php').chmod(0o600)
    (public/'.htaccess').chmod(0o600)
    print('Private deployment bundle prepared locally. Contains secrets: do not commit or publish private/.')


if __name__=='__main__': main()
