import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mailer.send import build_message, load_config, render_html, send_message


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'sender': 'AI News <news@example.com>', 'recipient': 'reader@example.com',
                    'smtp': {'host': 'smtp.example.com', 'port': 587, 'security': 'starttls'}}

    def test_html_keeps_sources_and_removes_unsafe_attributes(self):
        output = render_html('# Briefing\n\n**Résumé** [Source](https://example.com)\n\n'
                             '<img src="x" onerror="alert(1)"><a href="javascript:alert(1)">bad</a>')
        self.assertIn('<h1>Briefing</h1>', output)
        self.assertIn('<strong>Résumé</strong>', output)
        self.assertIn('href="https://example.com"', output)
        self.assertNotIn('javascript:', output)
        self.assertNotIn('onerror', output)
        self.assertNotIn('<img', output)

    def test_multipart_and_empty_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'briefing.md'
            path.write_text('# Actualités\n\nUne synthèse française.', encoding='utf-8')
            self.cfg['input'] = str(path)
            message = build_message(self.cfg)
            self.assertEqual(message['To'], 'reader@example.com')
            self.assertEqual(message.get_content_type(), 'multipart/alternative')
            self.assertIn('française', message.get_body(('plain',)).get_content())
            self.assertIn('<h1>', message.get_body(('html',)).get_content())
            path.write_text('')
            with self.assertRaises(ValueError):
                build_message(self.cfg)

    @patch.dict(os.environ, {'SMTP_USERNAME': 'user', 'SMTP_PASSWORD': 'secret'})
    @patch('mailer.send.smtplib.SMTP')
    def test_starttls_before_login_and_send(self, smtp):
        server = smtp.return_value.__enter__.return_value
        server.send_message.return_value = {}
        send_message(self.cfg, 'message')
        self.assertEqual([call[0] for call in server.method_calls],
                         ['ehlo', 'starttls', 'ehlo', 'login', 'send_message'])

    @patch.dict(os.environ, {'SMTP_USERNAME': 'user', 'SMTP_PASSWORD': 'secret'})
    @patch('mailer.send.smtplib.SMTP_SSL')
    def test_ssl_and_recipient_rejection(self, smtp):
        self.cfg['smtp'].update(security='ssl', port=465)
        server = smtp.return_value.__enter__.return_value
        server.send_message.return_value = {'reader@example.com': (550, b'rejected')}
        with self.assertRaises(RuntimeError):
            send_message(self.cfg, 'message')
        server.starttls.assert_not_called()
        self.assertEqual(smtp.call_args.kwargs['port'], 465)

    @patch.dict(os.environ, {}, clear=True)
    @patch('mailer.send.smtplib.SMTP')
    def test_missing_credentials_does_not_connect(self, smtp):
        with self.assertRaises(ValueError):
            send_message(self.cfg, 'message')
        smtp.assert_not_called()

    def test_reject_plaintext_auth_and_header_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mail.yaml'
            path.write_text('sender: a@example.com\nrecipient: b@example.com\n'
                            'smtp: {host: localhost, security: none, auth: true}\n')
            with self.assertRaises(ValueError):
                load_config(path)
            path.write_text('sender: a@example.com\nrecipient: "b@example.com\\nBcc: x@example.com"\n'
                            'smtp: {host: localhost, security: starttls}\n')
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == '__main__':
    unittest.main()
