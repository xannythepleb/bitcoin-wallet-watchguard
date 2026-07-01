from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .models import NotificationMessage, NotificationResult

logger = logging.getLogger("wwg.notifications.nostr")


class NostrNotificationProvider:
    name = "nostr"

    def __init__(
        self,
        *,
        helper_path: str,
        sender_nsec: str,
        recipients: list[Any],
        relays: list[str],
        min_successful_relays: int = 1,
        send_copy_to_self: bool = True,
        connect_timeout_seconds: int = 10,
        process_timeout_seconds: int = 30,
        configured_sender_npub: str = "",
    ) -> None:
        self.helper_path = helper_path
        self.sender_nsec = sender_nsec
        self.recipients = recipients
        self.relays = relays
        self.min_successful_relays = min_successful_relays
        self.send_copy_to_self = send_copy_to_self
        self.connect_timeout_seconds = max(1, connect_timeout_seconds)
        self.process_timeout_seconds = max(1, process_timeout_seconds)
        self.configured_sender_npub = configured_sender_npub

    async def send(self, message: NotificationMessage) -> NotificationResult:
        request = {
            "sender_nsec": self.sender_nsec,
            "message": self._nostr_message_text(message),
            "recipients": self.recipients,
            "relays": self.relays,
            "min_successful_relays": self.min_successful_relays,
            "send_copy_to_self": self.send_copy_to_self,
            "connect_timeout_seconds": self.connect_timeout_seconds,
        }

        output = await self._run_helper(request)
        result = self._result_from_helper_output(output)

        sender_npub = str(output.get("sender_npub") or "").strip()
        if (
            self.configured_sender_npub
            and sender_npub
            and sender_npub != self.configured_sender_npub
        ):
            logger.warning(
                "Configured Nostr sender npub does not match encrypted nsec; "
                "config=%s helper=%s",
                self.configured_sender_npub,
                sender_npub,
            )

        return result

    @staticmethod
    def _nostr_message_text(message: NotificationMessage) -> str:
        body = message.body_for_plain_text.strip()
        title = message.title.strip()
        if not body:
            return title
        if not title:
            return body
        return f"{title}\n\n{body}"

    async def _run_helper(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        logger.info(
            "Sending Nostr encrypted DM via %s to %d recipient(s) across %d relay(s)",
            self.helper_path,
            len(self.recipients),
            len(self.relays),
        )

        process = await asyncio.create_subprocess_exec(
            self.helper_path,
            "send-dm",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=self.process_timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise TimeoutError(
                f"Nostr helper timed out after {self.process_timeout_seconds}s"
            ) from exc

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            detail = stderr_text or stdout_text or f"exit code {process.returncode}"
            raise RuntimeError(f"Nostr helper send-dm failed: {detail}")

        if not stdout_text:
            raise RuntimeError("Nostr helper send-dm returned no JSON output")

        try:
            parsed = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Nostr helper send-dm returned invalid JSON") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("Nostr helper send-dm returned unexpected JSON")

        return parsed

    def _result_from_helper_output(self, output: dict[str, Any]) -> NotificationResult:
        ok = bool(output.get("ok", False))
        detail = self._delivery_detail(output)
        if ok:
            logger.debug("Nostr helper accepted encrypted DM: %s", detail)
        else:
            logger.warning("Nostr helper reported delivery failure: %s", detail)
        return NotificationResult(provider=self.name, ok=ok, detail=detail)

    @staticmethod
    def _delivery_detail(output: dict[str, Any]) -> str:
        deliveries = []
        recipients = output.get("recipients") or []
        if isinstance(recipients, list):
            deliveries.extend(item for item in recipients if isinstance(item, dict))

        self_copy = output.get("self_copy")
        if isinstance(self_copy, dict):
            deliveries.append(self_copy)

        successful = sum(1 for delivery in deliveries if bool(delivery.get("ok", False)))
        accepted_relays = sum(
            len(delivery.get("accepted_relays") or [])
            for delivery in deliveries
            if isinstance(delivery.get("accepted_relays") or [], list)
        )
        failed = [
            delivery for delivery in deliveries if not bool(delivery.get("ok", False))
        ]

        if not failed:
            return (
                f"delivered to {successful}/{len(deliveries)} recipient(s); "
                f"accepted by {accepted_relays} relay publish(es)"
            )

        failed_details = []
        for delivery in failed:
            label = str(delivery.get("name") or delivery.get("npub") or "recipient")
            relay_failures = delivery.get("failed_relays") or []
            if isinstance(relay_failures, list) and relay_failures:
                relay_summary = ", ".join(
                    str(item.get("url") or "relay")
                    for item in relay_failures
                    if isinstance(item, dict)
                )
                failed_details.append(f"{label} failed via {relay_summary or 'relay'}")
            else:
                failed_details.append(f"{label} failed")

        return (
            f"delivered to {successful}/{len(deliveries)} recipient(s); "
            + "; ".join(failed_details)
        )
