from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .models import NotificationMessage, NotificationResult


class NotificationProvider(Protocol):
    name: str

    async def send(self, message: NotificationMessage) -> NotificationResult:
        ...


class NotificationDeliveryError(RuntimeError):
    def __init__(self, results: Sequence[NotificationResult]) -> None:
        self.results = list(results)
        failures = [result for result in self.results if not result.ok]
        details = "; ".join(
            f"{result.provider}: {result.detail or 'failed'}" for result in failures
        )
        super().__init__(f"Notification delivery failed: {details}")


class NotificationManager:
    def __init__(self, providers: Sequence[NotificationProvider]) -> None:
        self.providers = list(providers)

    async def send(
        self,
        message: NotificationMessage,
        *,
        raise_on_failure: bool = True,
    ) -> list[NotificationResult]:
        results: list[NotificationResult] = []

        for provider in self.providers:
            try:
                results.append(await provider.send(message))
            except Exception as exc:
                results.append(
                    NotificationResult(
                        provider=provider.name,
                        ok=False,
                        detail=str(exc),
                    )
                )

        if raise_on_failure and any(not result.ok for result in results):
            raise NotificationDeliveryError(results)

        return results
