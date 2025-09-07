from __future__ import annotations

"""
Firstmail provider.

Примечание: публичной стабильной документации Firstmail API может не быть.
Для начала сделана заглушка через домены-псевдонимы и локальный storage.
Замените реализацию на реальный API/IMAP позже.
"""

from dataclasses import dataclass
from typing import List
import itertools

from .email_provider import EmailProvider, Inbox, MailMessage


_local_counter = itertools.count(1)


@dataclass
class _LocalStore:
    inboxes: list[Inbox]
    messages: dict[str, list[MailMessage]]


_STORE = _LocalStore(inboxes=[], messages={})


class FirstmailProvider(EmailProvider):
    def __init__(self, domain: str = "firstmail.ltd") -> None:
        self.domain = domain

    def create_inbox(self) -> Inbox:
        local_id = next(_local_counter)
        address = f"wot{local_id}@{self.domain}"
        inbox = Inbox(address=address, id=str(local_id))
        _STORE.inboxes.append(inbox)
        _STORE.messages[inbox.address] = []
        return inbox

    def list_messages(self, inbox: Inbox) -> List[MailMessage]:
        return list(_STORE.messages.get(inbox.address, []))

    # Тестовый helper для имитации письма (можно использовать в e2e тесте)
    def _inject_message(self, inbox: Inbox, msg: MailMessage) -> None:
        _STORE.messages.setdefault(inbox.address, []).append(msg)


