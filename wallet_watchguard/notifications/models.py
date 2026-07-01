from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationMessage:
    title: str
    markdown_body: str
    plain_body: str | None = None
    priority: str | None = None
    tags: str | None = None

    @property
    def body_for_plain_text(self) -> str:
        return self.plain_body if self.plain_body is not None else self.markdown_body


@dataclass(frozen=True)
class NotificationResult:
    provider: str
    ok: bool
    detail: str = ""
