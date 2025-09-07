from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence
import time


@dataclass(frozen=True)
class Inbox:
    address: str
    id: str | None = None


@dataclass(frozen=True)
class MailMessage:
    subject: str
    from_address: str
    html_body: str | None
    text_body: str | None


class EmailProvider(Protocol):
    """Abstract email provider capable of creating an inbox and fetching messages."""

    def create_inbox(self) -> Inbox:
        ...

    def list_messages(self, inbox: Inbox) -> list[MailMessage]:
        ...

    def wait_for_message(
        self,
        inbox: Inbox,
        subject_contains: Sequence[str] | None = None,
        from_contains: Sequence[str] | None = None,
        timeout_sec: int = 180,
        poll_interval_sec: float = 3.0,
    ) -> MailMessage | None:
        deadline = time.time() + timeout_sec
        subject_contains = subject_contains or []
        from_contains = from_contains or []
        while time.time() < deadline:
            for msg in self.list_messages(inbox):
                s_ok = (not subject_contains) or any(s.lower() in (msg.subject or "").lower() for s in subject_contains)
                f_ok = (not from_contains) or any(f.lower() in (msg.from_address or "").lower() for f in from_contains)
                if s_ok and f_ok:
                    return msg
            time.sleep(poll_interval_sec)
        return None


