"""Reuse the persistent encrypted integration store; never expose tokens to LLMs."""
import os
from pathlib import Path
from uuid import UUID
from cryptography.fernet import InvalidToken
from app.communications.mail import MailStore


class CommentSecrets(MailStore):
    def path(self, channel_id):
        return self.root / (str(UUID(str(channel_id))) + ".comments")

    def save_token(self, channel_id, token):
        if not token or len(token) > 8000 or any(c in token for c in "\r\n"):
            raise ValueError("Некорректный токен доступа")
        with self.locked():
            payload = self.cipher().encrypt(token.encode())
            path = self.path(channel_id)
            temporary = Path(str(path) + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)

    def load_token(self, channel_id):
        with self.locked():
            try:
                return self.cipher().decrypt(self.path(channel_id).read_bytes()).decode()
            except (InvalidToken, FileNotFoundError):
                raise ValueError("Подключите аккаунт заново") from None
