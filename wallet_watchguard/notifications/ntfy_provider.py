from __future__ import annotations

from .models import NotificationMessage, NotificationResult
from ..ntfy import NtfyNotifier


class NtfyNotificationProvider:
    name = "ntfy"

    def __init__(self, notifier: NtfyNotifier) -> None:
        self.notifier = notifier

    async def send(self, message: NotificationMessage) -> NotificationResult:
        await self.notifier.send(
            message.title,
            message.markdown_body,
            priority=message.priority,
            tags=message.tags,
        )
        return NotificationResult(provider=self.name, ok=True)
