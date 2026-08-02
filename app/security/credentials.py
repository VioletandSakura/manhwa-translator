from __future__ import annotations

from typing import Protocol

import keyring


class CredentialBackend(Protocol):
    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class CredentialStore:
    SERVICE_NAME = "MangaTranslatorPersonal"

    def __init__(self, backend: CredentialBackend = keyring) -> None:
        self._backend = backend

    def save(self, channel_id: str, api_key: str) -> None:
        if not channel_id.strip() or not api_key.strip():
            raise ValueError("通道编号和API Key不能为空")
        self._backend.set_password(self.SERVICE_NAME, channel_id, api_key)

    def load(self, channel_id: str) -> str | None:
        return self._backend.get_password(self.SERVICE_NAME, channel_id)

    def revoke(self, channel_id: str) -> None:
        try:
            self._backend.delete_password(self.SERVICE_NAME, channel_id)
        except keyring.errors.PasswordDeleteError:
            return

