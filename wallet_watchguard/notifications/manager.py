from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from .models import NotificationMessage, NotificationResult

logger = logging.getLogger("wwg.notifications")


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

        if not self.providers:
            logger.warning("No notification providers configured; %r not delivered", message.title)
            return results

        provider_names = ", ".join(p.name for p in self.providers)
        logger.info("Dispatching %r to %d provider(s): %s", message.title, len(self.providers), provider_names)

        for provider in self.providers:
            try:
                result = await provider.send(message)
                results.append(result)
                if result.ok:
                    logger.debug("Provider %s delivered %r", provider.name, message.title)
                else:
                    logger.warning(
                        "Provider %s reported delivery failure for %r: %s",
                        provider.name, message.title, result.detail or "failed",
                    )
            except Exception as exc:
                logger.warning("Provider %s failed to deliver %r: %s", provider.name, message.title, exc)
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