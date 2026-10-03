"""Encrypted per-product SMTP configuration; TLS-only, pinned public destination."""
import fcntl
import ipaddress
import json
import os
import re
import smtplib
import socket
import ssl
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from uuid import UUID
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def email_address(value: str):
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', value):
        raise ValueError('Ожидается один email-адрес без имени и переносов строк')
    return value

class SMTPConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    host: str = Field(min_length=3, max_length=253)
    port: int = Field(default=465)
    username: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(repr=False)
    from_email: str
    from_name: str = Field(default='', max_length=100)

    @field_validator('host')
    @classmethod
    def host_name(cls, value):
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value) or '.' not in value:
            raise ValueError('Ожидается публичное имя SMTP-сервера')
        return value.lower()

    @field_validator('port')
    @classmethod
    def tls_port(cls, value):
        if value not in {465, 587}:
            raise ValueError('Поддерживаются TLS 465 или STARTTLS 587')
        return value

    @field_validator('from_email')
    @classmethod
    def address(cls, value):
        return email_address(value)

    @field_validator('username', 'from_name')
    @classmethod
    def no_headers(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('Недопустимые символы')
        return value

    @field_validator('password')
    @classmethod
    def not_empty(cls, value):
        if not value.get_secret_value():
            raise ValueError('Пароль приложения не задан')
        return value

class MailStore:
    def __init__(self, root: Path):
        self.root = Path(root) / '_integrations'

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.root / '.lock', 'a') as handle:
            os.chmod(handle.name, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def cipher(self):
        path = self.root / 'mail.key'
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        return Fernet(path.read_bytes())

    def path(self, product_id):
        return self.root / (str(UUID(str(product_id))) + '.smtp')

    def save(self, product_id, config: SMTPConfig):
        with self.locked():
            data = config.model_dump(mode='json', exclude={'password'})
            data['password'] = config.password.get_secret_value()
            encrypted = self.cipher().encrypt(json.dumps(data).encode())
            path = self.path(product_id)
            tmp = path.with_suffix('.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(encrypted)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)

    def load(self, product_id):
        with self.locked():
            path = self.path(product_id)
            if not path.exists():
                return None
            try:
                return SMTPConfig.model_validate_json(self.cipher().decrypt(path.read_bytes()))
            except (InvalidToken, ValueError):
                raise ValueError("Mailbox configuration could not be decrypted; reconnect mail") from None

    def delete(self, product_id):
        with self.locked():
            self.path(product_id).unlink(missing_ok=True)


def public_ip(host: str, port: int):
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not rows or any(not ipaddress.ip_address(r[4][0]).is_global for r in rows):
        raise ValueError('SMTP destination must resolve to public IPs only')
    return rows[0][4][0]

class PinnedSMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        return socket.create_connection((public_ip(host, port), port), timeout)

class PinnedSMTPSSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        raw = socket.create_connection((public_ip(host, port), port), timeout)
        try:
            return self.context.wrap_socket(raw, server_hostname=host)
        except BaseException:
            raw.close()
            raise

@contextmanager
def connection(config):
    context = ssl.create_default_context()
    smtp = (PinnedSMTPSSL(config.host, config.port, timeout=20, context=context)
            if config.port == 465 else PinnedSMTP(config.host, config.port, timeout=20))
    try:
        smtp.ehlo()
        if config.port == 587:
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(config.username, config.password.get_secret_value())
        yield smtp
    finally:
        # QUIT failure after successful DATA must not turn delivery into a retry.
        smtp.close()

def check_connection(config):
    with connection(config) as smtp:
        code, _ = smtp.noop()
        if code != 250:
            raise ValueError('SMTP check failed')

def send_email(config, *, recipient: str, subject: str, body: str, message_id: str):
    email_address(recipient)
    if any(ord(c) < 32 for c in subject) or len(subject) > 300 or not body or len(body) > 20000:
        raise ValueError('Invalid email content')
    message = EmailMessage()
    from email.headerregistry import Address
    local, domain = config.from_email.rsplit('@', 1)
    message['From'] = Address(config.from_name, local, domain)
    message['To'], message['Subject'] = recipient, subject
    message['Date'], message['Message-ID'] = formatdate(localtime=False), message_id
    message.set_content(body)
    with connection(config) as smtp:
        refused = smtp.send_message(message, from_addr=config.from_email, to_addrs=[recipient])
        if refused:
            raise ValueError('SMTP recipient refused')
