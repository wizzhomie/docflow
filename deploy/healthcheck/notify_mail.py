"""Send a healthcheck verdict by e-mail, when SMTP settings are present.

The check must never fail because mail could not be sent, so every error here is
reported and swallowed. Settings come from the environment, so credentials live in
`~/.config/docflow/alerts.env` and never in the repository:

    DOCFLOW_SMTP_HOST=smtp.example.com
    DOCFLOW_SMTP_PORT=587
    DOCFLOW_SMTP_USER=docflow@example.com
    DOCFLOW_SMTP_PASSWORD=app-password
    DOCFLOW_SMTP_FROM=docflow@example.com
    DOCFLOW_SMTP_TO=owner@example.com

Run: python3 notify_mail.py fail "<what broke>"   (or `ok` for a recovery)
"""

from __future__ import annotations

import os
import smtplib
import socket
import ssl
import sys
from datetime import UTC, datetime
from email.message import EmailMessage

KEYS = ('HOST', 'PORT', 'USER', 'PASSWORD', 'FROM', 'TO')
SUBJECTS = {
    'fail': 'DocFlow: the demo is not answering',
    'ok': 'DocFlow: the demo is answering again',
}


def settings() -> dict[str, str]:
    """SMTP settings from the environment; empty values mean "not configured"."""
    values = {key: os.environ.get(f'DOCFLOW_SMTP_{key}', '').strip() for key in KEYS}
    values.setdefault('PORT', '')
    if not values['PORT']:
        values['PORT'] = '587'
    return values


def configured(values: dict[str, str]) -> bool:
    return bool(values.get('HOST') and values.get('TO'))


def build(verdict: str, text: str, values: dict[str, str]) -> EmailMessage:
    """One readable mail: what happened, on which machine, and when."""
    when = datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')
    message = EmailMessage()
    message['Subject'] = SUBJECTS.get(verdict, 'DocFlow: healthcheck')
    message['From'] = values.get('FROM') or values.get('USER') or 'docflow@localhost'
    message['To'] = values['TO']
    message.set_content(
        f'{text.strip()}\n\nHost: {socket.gethostname()}\nTime: {when}\n'
    )
    return message


def send(message: EmailMessage, values: dict[str, str]) -> bool:
    """True when the mail left; a refused relay is reported, never raised."""
    try:
        port = int(values.get('PORT') or 587)
        with smtplib.SMTP(values['HOST'], port, timeout=20) as client:
            client.ehlo()
            if client.has_extn('starttls'):
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if values.get('USER'):
                client.login(values['USER'], values.get('PASSWORD', ''))
            client.send_message(message)
        return True
    except (OSError, smtplib.SMTPException, ValueError) as error:
        print(f'notify_mail: not sent ({error})', file=sys.stderr)
        return False


def main(argv: list[str]) -> int:
    verdict = argv[1] if len(argv) > 1 else 'fail'
    text = ' '.join(argv[2:]) or 'healthcheck failed'
    values = settings()
    if not configured(values):
        # The channel is simply not set up yet: a silent no-op, not a failure.
        return 0
    send(build(verdict, text, values), values)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
