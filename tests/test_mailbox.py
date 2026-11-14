"""Deterministic checks for the mailbox (IMAP) intake.

The IMAP client is exercised against a local fake of the protocol, so the socket
path is real while no mail account is needed. The MIME parser is checked with
messages built in memory.

Run: python3 tests/test_mailbox.py
"""

import email
import os
import socketserver
import sys
import tempfile
import threading
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mailbox  # noqa: E402

PDF_BYTES = b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n'


def build_message(pdf_name='Рахунок_2026-114.pdf', message_id='<m-114@example.com>', pdf_bytes=None):
    msg = EmailMessage()
    msg['Message-ID'] = message_id
    msg['From'] = 'Постачальник <billing@example.com>'
    msg['Subject'] = 'Рахунок №2026-114'
    msg['Date'] = 'Fri, 14 Nov 2026 10:00:00 +0200'
    msg.set_content('Добрий день, рахунок у вкладенні.')
    msg.add_attachment(pdf_bytes or PDF_BYTES, maintype='application', subtype='pdf', filename=pdf_name)
    return msg.as_bytes()


class FakeIMAP:
    """Minimal IMAP4 server: one message, UNSEEN/Seen flag, optional bad login."""

    def __init__(self, raw, password='secret', respect_seen=True):
        fake = self
        self.raw = raw
        self.password = password
        self.respect_seen = respect_seen
        self.seen = False
        self.fetches = 0

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b'* OK fake IMAP ready\r\n')
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    text = line.decode('utf-8', 'replace').strip()
                    if not text:
                        continue
                    tag, _, rest = text.partition(' ')
                    command, _, arguments = rest.partition(' ')
                    command = command.upper()
                    reply = []
                    if command == 'CAPABILITY':
                        reply.append(b'* CAPABILITY IMAP4rev1')
                    elif command == 'LOGIN':
                        supplied = arguments.split(' ')[-1].strip('"')
                        if supplied != fake.password:
                            self.wfile.write(f'{tag} NO [AUTHENTICATIONFAILED] LOGIN failed\r\n'.encode())
                            continue
                        reply.append(b'* OK logged in')
                    elif command in ('SELECT', 'EXAMINE'):
                        reply.append(b'* 1 EXISTS')
                        reply.append(b'* FLAGS (\\Seen)')
                    elif command == 'UID':
                        operation, _, uid_args = arguments.partition(' ')
                        operation = operation.upper()
                        if operation == 'SEARCH':
                            if fake.seen and fake.respect_seen:
                                reply.append(b'* SEARCH')
                            else:
                                reply.append(b'* SEARCH 1')
                        elif operation == 'FETCH':
                            fake.fetches += 1
                            self.wfile.write(
                                b'* 1 FETCH (RFC822 {%d}\r\n' % len(fake.raw) + fake.raw + b')\r\n'
                            )
                            self.wfile.write(f'{tag} OK FETCH completed\r\n'.encode())
                            continue
                        elif operation == 'STORE':
                            if fake.respect_seen:
                                fake.seen = True
                            reply.append(b'* 1 FETCH (FLAGS (\\Seen))')
                    elif command == 'LOGOUT':
                        self.wfile.write(b'* BYE logging out\r\n')
                        self.wfile.write(f'{tag} OK LOGOUT completed\r\n'.encode())
                        return
                    elif command == 'NOOP':
                        pass
                    else:
                        self.wfile.write(f'{tag} BAD unknown command\r\n'.encode())
                        continue
                    payload = b''
                    for item in reply:
                        payload += item + b'\r\n'
                    payload += f'{tag} OK {command} completed\r\n'.encode()
                    self.wfile.write(payload)

            def log_message(self, *args):
                pass

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Env:
    """Point mailbox settings at a fake server and a throwaway state file."""

    def __init__(self, port=None, password='secret'):
        self.port = port
        self.password = password

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_state = mailbox.STATE
        self.saved_env = {key: os.environ.get(key) for key in self._keys()}
        mailbox.STATE = Path(self.tmp.name) / 'mailbox_state.json'
        for key in self._keys():
            os.environ.pop(key, None)
        if self.port is not None:
            os.environ.update({
                'DOCFLOW_IMAP_HOST': '127.0.0.1',
                'DOCFLOW_IMAP_PORT': str(self.port),
                'DOCFLOW_IMAP_USER': 'docflow@example.com',
                'DOCFLOW_IMAP_PASSWORD': self.password,
                'DOCFLOW_IMAP_SSL': '0',
                'DOCFLOW_IMAP_INTERVAL': '10',
            })
        return self

    def __exit__(self, *exc):
        mailbox.STATE = self.saved_state
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()
        return False

    @staticmethod
    def _keys():
        return [
            'DOCFLOW_IMAP_HOST', 'DOCFLOW_IMAP_PORT', 'DOCFLOW_IMAP_USER',
            'DOCFLOW_IMAP_PASSWORD', 'DOCFLOW_IMAP_SSL', 'DOCFLOW_IMAP_FOLDER',
            'DOCFLOW_IMAP_INTERVAL',
        ]


def test_parses_pdf_attachment():
    raw = build_message()
    msg = email.message_from_bytes(raw)
    attachments = mailbox.message_attachments(msg)
    assert len(attachments) == 1, attachments
    attachment = attachments[0]
    assert attachment['filename'] == 'Рахунок_2026-114.pdf', attachment['filename']
    assert attachment['content_type'] == 'application/pdf', attachment['content_type']
    assert attachment['data'] == PDF_BYTES, attachment['size']


def test_ignores_message_without_attachments():
    msg = EmailMessage()
    msg['Subject'] = 'just text'
    msg.set_content('no files here')
    assert mailbox.message_attachments(email.message_from_bytes(msg.as_bytes())) == []


def test_not_configured_is_reported_not_raised():
    with Env():
        result = mailbox.poll(lambda attachment, message: True)
        assert result['messages'] == 0, result
        status = mailbox.status()
        assert status['configured'] is False, status
        assert 'not configured' in (status['last_error'] or ''), status


def test_poll_ingests_attachment_and_marks_message_seen():
    fake = FakeIMAP(build_message())
    try:
        with Env(port=fake.port):
            taken = []

            def handler(attachment, message):
                taken.append((attachment['filename'], message['subject']))
                return True

            first = mailbox.poll(handler)
            assert first['messages'] == 1, first
            assert first['attachments'] == 1, first
            assert taken and taken[0][0] == 'Рахунок_2026-114.pdf', taken
            assert taken[0][1] == 'Рахунок №2026-114', taken
            assert fake.seen is True, 'message was not marked as seen'

            second = mailbox.poll(handler)
            assert second['messages'] == 0, second
            assert second['attachments'] == 0, second
            assert len(taken) == 1, taken
            status = mailbox.status()
            assert status['messages'] == 1, status
            assert status['attachments'] == 1, status
            assert status['last_error'] is None, status
    finally:
        fake.close()


def test_failed_attachment_is_retried_on_the_next_poll():
    fake = FakeIMAP(build_message())
    try:
        with Env(port=fake.port):
            attempts = []

            def handler(attachment, message):
                attempts.append(attachment['filename'])
                return False

            first = mailbox.poll(handler)
            assert first['messages'] == 0, first
            assert 'could not be ingested' in (mailbox.status()['last_error'] or ''), mailbox.status()
            assert fake.seen is False, 'a failed message must stay unread'

            second = mailbox.poll(handler)
            assert len(attempts) == 2, attempts
            assert second['messages'] == 0, second
    finally:
        fake.close()


def test_ignored_attachment_marks_message_seen_but_is_not_counted():
    fake = FakeIMAP(build_message())
    try:
        with Env(port=fake.port):
            result = mailbox.poll(lambda attachment, message: None)
            assert result['messages'] == 1, result
            assert result['attachments'] == 0, result
            assert result['ignored'] == 1, result
            assert fake.seen is True
    finally:
        fake.close()


def test_stored_message_id_prevents_duplicate_processing():
    # A server that keeps reporting the message as UNSEEN must not cause the
    # same document to be queued twice.
    fake = FakeIMAP(build_message(), respect_seen=False)
    try:
        with Env(port=fake.port):
            calls = []
            mailbox.poll(lambda attachment, message: calls.append(attachment['filename']) or True)
            mailbox.poll(lambda attachment, message: calls.append(attachment['filename']) or True)
            assert len(calls) == 1, calls
    finally:
        fake.close()


def test_login_error_is_surfaced():
    fake = FakeIMAP(build_message())
    try:
        with Env(port=fake.port, password='wrong'):
            result = mailbox.poll(lambda attachment, message: True)
            assert result['messages'] == 0, result
            status = mailbox.status()
            assert status['configured'] is True, status
            assert 'LOGIN failed' in (status['last_error'] or ''), status
    finally:
        fake.close()


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('mailbox checks passed')


if __name__ == '__main__':
    main()
